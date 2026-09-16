import Darwin
import Foundation

public struct ServerConfiguration {
    public var executable: URL
    public var arguments: [String]
    public var environment: [String: String]
    public var logFile: URL
    public var readyTimeout: TimeInterval
    public var stopGracePeriod: TimeInterval

    public init(executable: URL, arguments: [String], environment: [String: String], logFile: URL,
                readyTimeout: TimeInterval = 60, stopGracePeriod: TimeInterval = 10) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.logFile = logFile
        self.readyTimeout = readyTimeout
        self.stopGracePeriod = stopGracePeriod
    }
}

public enum ServerFailure: Error, Equatable {
    case launchFailed(String)
    case locked(pid: String)
    case malformedLine(String)
    case readyTimeout(seconds: TimeInterval)
    case exitedBeforeReady(status: Int32)
    case logUnavailable(String)
}

public enum ServerEvent: Equatable {
    case ready(port: Int)
    case failed(ServerFailure)
}

public enum StopOutcome: Equatable {
    case notRunning
    case stoppedGracefully
    case killed
}

/// Starts the command-center server as its own process-group leader, reads its protocol
/// line, writes all of its output to the launch log, and stops it with escalation.
public final class ServerController {
    public var onExit: ((Int32) -> Void)?

    private let configuration: ServerConfiguration
    private let lock = NSLock()
    private var childPID: pid_t?
    private var started = false
    private var resolved = false
    private var becameReady = false
    private var stopping = false
    private var exited = false
    private let exitSignal = DispatchSemaphore(value: 0)
    private let outputDrained = DispatchSemaphore(value: 0)

    public init(configuration: ServerConfiguration) {
        self.configuration = configuration
    }

    public var pid: pid_t? {
        lock.lock(); defer { lock.unlock() }
        return childPID
    }

    public func start(completion: @escaping (ServerEvent) -> Void) {
        lock.lock()
        guard !started else {
            lock.unlock()
            DispatchQueue.main.async { completion(.failed(.launchFailed("server already started"))) }
            return
        }
        started = true
        lock.unlock()

        guard FileManager.default.createFile(atPath: configuration.logFile.path, contents: nil),
              let log = try? FileHandle(forWritingTo: configuration.logFile) else {
            _ = resolve(.failed(.logUnavailable(configuration.logFile.path)), completion)
            return
        }

        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else {
            try? log.close()
            _ = resolve(.failed(.launchFailed(String(cString: strerror(errno)))), completion)
            return
        }
        let (readEnd, writeEnd) = (fds[0], fds[1])

        let spawned: pid_t
        do {
            spawned = try spawnGroupLeader(outputFD: writeEnd)
        } catch let failure as ServerFailure {
            close(readEnd); close(writeEnd); try? log.close()
            _ = resolve(.failed(failure), completion)
            return
        } catch {
            close(readEnd); close(writeEnd); try? log.close()
            _ = resolve(.failed(.launchFailed("\(error)")), completion)
            return
        }
        close(writeEnd)
        lock.lock(); childPID = spawned; lock.unlock()

        Thread.detachNewThread { [self] in readOutput(from: readEnd, into: log, completion: completion) }
        Thread.detachNewThread { [self] in waitForExit(of: spawned, completion: completion) }
        DispatchQueue.global().asyncAfter(deadline: .now() + configuration.readyTimeout) { [self] in
            if resolve(.failed(.readyTimeout(seconds: configuration.readyTimeout)), completion) {
                killpg(spawned, SIGKILL)
            }
        }
    }

    @discardableResult
    public func stop() -> StopOutcome {
        lock.lock()
        guard let target = childPID, !exited else { lock.unlock(); return .notRunning }
        stopping = true
        lock.unlock()

        kill(target, SIGTERM)
        if exitSignal.wait(timeout: .now() + configuration.stopGracePeriod) == .success {
            guard killpg(target, 0) == 0 else { return .stoppedGracefully }
            killpg(target, SIGKILL)  // the server exited but left children in its group
            return .killed
        }
        killpg(target, SIGKILL)
        _ = exitSignal.wait(timeout: .now() + 5)
        return .killed
    }

    // MARK: - Internals

    private func spawnGroupLeader(outputFD: Int32) throws -> pid_t {
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_adddup2(&actions, outputFD, 1)
        posix_spawn_file_actions_adddup2(&actions, outputFD, 2)

        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT))
        posix_spawnattr_setpgroup(&attributes, 0)

        let path = configuration.executable.path
        let argv: [UnsafeMutablePointer<CChar>?] = ([path] + configuration.arguments).map { strdup($0) } + [nil]
        let envp: [UnsafeMutablePointer<CChar>?] = configuration.environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            argv.forEach { free($0) }
            envp.forEach { free($0) }
        }

        var spawned: pid_t = 0
        let result = posix_spawn(&spawned, path, &actions, &attributes, argv, envp)
        guard result == 0 else { throw ServerFailure.launchFailed("\(path): \(String(cString: strerror(result)))") }
        return spawned
    }

    private func readOutput(from fd: Int32, into log: FileHandle, completion: @escaping (ServerEvent) -> Void) {
        var pending = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = read(fd, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { break }
            let chunk = Data(buffer[0..<count])
            log.write(chunk)
            pending.append(chunk)
            while let newline = pending.firstIndex(of: 0x0A) {
                let line = String(decoding: pending[pending.startIndex..<newline], as: UTF8.self)
                pending.removeSubrange(pending.startIndex...newline)
                handle(line: line, completion: completion)
            }
        }
        close(fd)
        try? log.close()
        outputDrained.signal()
    }

    private func handle(line: String, completion: @escaping (ServerEvent) -> Void) {
        switch ServerLine.parse(line) {
        case .ready(let port)?:
            resolveReady(port: port, completion)
        case .locked(let holder)?:
            resolve(.failed(.locked(pid: holder)), completion)
        case .malformed(let text)?:
            if resolve(.failed(.malformedLine(text)), completion), let target = pid {
                killpg(target, SIGKILL)
            }
        case nil:
            break
        }
    }

    private func waitForExit(of target: pid_t, completion: @escaping (ServerEvent) -> Void) {
        var raw: Int32 = 0
        while waitpid(target, &raw, 0) < 0 && errno == EINTR {}
        let signalNumber = raw & 0x7f
        let status: Int32 = signalNumber == 0 ? (raw >> 8) & 0xff : 128 + signalNumber

        // Read what the server printed last (a LOCKED line, a traceback) before deciding.
        // Children that still hold the pipe open must not block this, so the wait is bounded.
        _ = outputDrained.wait(timeout: .now() + 1)

        lock.lock()
        exited = true
        let wasReady = becameReady
        let wasStopping = stopping
        lock.unlock()
        exitSignal.signal()

        if !resolve(.failed(.exitedBeforeReady(status: status)), completion), wasReady, !wasStopping {
            DispatchQueue.main.async { [self] in onExit?(status) }
        }
    }

    /// Delivers the first event only. Returns true if this call delivered it.
    @discardableResult
    private func resolve(_ event: ServerEvent, _ completion: @escaping (ServerEvent) -> Void) -> Bool {
        lock.lock()
        guard !resolved else { lock.unlock(); return false }
        resolved = true
        lock.unlock()
        DispatchQueue.main.async { completion(event) }
        return true
    }

    /// Atomically records readiness only when READY wins the startup race.
    private func resolveReady(port: Int, _ completion: @escaping (ServerEvent) -> Void) {
        lock.lock()
        guard !resolved else { lock.unlock(); return }
        resolved = true
        becameReady = true
        lock.unlock()
        DispatchQueue.main.async { completion(.ready(port: port)) }
    }
}
