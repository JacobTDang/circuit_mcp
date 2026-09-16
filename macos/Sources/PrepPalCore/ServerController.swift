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

/// The environment carries the OpenRouter key. "Never print this" was a comment at the one call
/// site that builds it, which nothing enforced: an `NSLog("\(configuration)")` left behind while
/// debugging would have written the key into the log the app offers to open. Rendering a
/// configuration therefore names its environment and never shows a value of it.
extension ServerConfiguration: CustomStringConvertible, CustomDebugStringConvertible {
    public var description: String {
        let names = environment.keys.sorted().joined(separator: ", ")
        return """
            ServerConfiguration(executable: \(executable.path), arguments: \(arguments), \
            environment: [\(names)] (values redacted), logFile: \(logFile.path), \
            readyTimeout: \(readyTimeout), stopGracePeriod: \(stopGracePeriod))
            """
    }

    public var debugDescription: String { description }
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
    /// SIGKILL was sent but the process group could not be confirmed gone, and why.
    case killFailed(reason: String)
}

/// Starts the command-center server as its own process-group leader, reads its protocol
/// line, writes all of its output to the launch log, and stops it with escalation.
public final class ServerController {
    /// How long `stop()` waits for a force-killed process to report its exit.
    private static let killExitWait: TimeInterval = 5
    /// How long `stop()` then waits for that process's group to empty.
    private static let groupEmptyWait: TimeInterval = 2

    public var onExit: ((Int32) -> Void)?

    private let configuration: ServerConfiguration
    private let lock = NSLock()
    private var childPID: pid_t?
    private var started = false
    private var resolved = false
    /// Readable by the tests that pin the startup race; only `resolveReady` may set it.
    private(set) var becameReady = false
    private var stopping = false
    private var exited = false
    private let exitSignal = DispatchSemaphore(value: 0)
    private let outputDrained = DispatchSemaphore(value: 0)

    public init(configuration: ServerConfiguration) {
        self.configuration = configuration
    }

    /// The longest `stop()` can block: the grace period, then the wait for the killed process to
    /// report its exit, then the wait for its group to empty. The app quotes this while it waits
    /// for a quit, so it is derived from the escalation below rather than restated there.
    public var worstCaseStopSeconds: TimeInterval {
        configuration.stopGracePeriod + Self.killExitWait + Self.groupEmptyWait
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
            // Only ESRCH means the group is empty. Any other errno is a check that did not
            // happen, and reporting that as a clean stop would hide a still-running group.
            if killpg(target, 0) != 0 {
                let failure = errno
                guard failure == ESRCH else {
                    return .killFailed(reason: "killpg(\(target), 0) after SIGTERM: \(String(cString: strerror(failure)))")
                }
                return .stoppedGracefully
            }
            killpg(target, SIGKILL)  // the server exited but left children in its group
            // The children were force-killed, so this branch owes the same confirmation the
            // escalation branch does: a group that did not empty is not a successful stop.
            if let survivor = groupSurvivor(target, within: Self.groupEmptyWait) {
                return .killFailed(reason: survivor)
            }
            return .killed
        }
        killpg(target, SIGKILL)
        // A SIGKILL that did not take is the orphan this class exists to prevent, so neither
        // the wait nor the group's survival may be reported to the caller as success.
        guard exitSignal.wait(timeout: .now() + Self.killExitWait) == .success else {
            return .killFailed(reason: "process \(target) did not report its exit within \(Self.killExitWait)s of SIGKILL")
        }
        if let survivor = groupSurvivor(target, within: Self.groupEmptyWait) {
            return .killFailed(reason: survivor)
        }
        return .killed
    }

    // MARK: - Internals

    private func spawnGroupLeader(outputFD: Int32) throws -> pid_t {
        var actions: posix_spawn_file_actions_t?
        try require("posix_spawn_file_actions_init", posix_spawn_file_actions_init(&actions))
        defer { posix_spawn_file_actions_destroy(&actions) }
        try require("posix_spawn_file_actions_addopen(/dev/null)",
                    posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0))
        try require("posix_spawn_file_actions_adddup2(stdout)",
                    posix_spawn_file_actions_adddup2(&actions, outputFD, 1))
        try require("posix_spawn_file_actions_adddup2(stderr)",
                    posix_spawn_file_actions_adddup2(&actions, outputFD, 2))

        var attributes: posix_spawnattr_t?
        try require("posix_spawnattr_init", posix_spawnattr_init(&attributes))
        defer { posix_spawnattr_destroy(&attributes) }
        // Without SETPGROUP the child joins this process's group, and every killpg here would
        // aim at a group id that does not exist: a server that can never be force-killed.
        try require("posix_spawnattr_setflags",
                    posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT)))
        try require("posix_spawnattr_setpgroup", posix_spawnattr_setpgroup(&attributes, 0))

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

    /// posix_spawn setup calls return an errno rather than setting one. A failure leaves the
    /// child misconfigured in a way nothing downstream can detect, so it fails the launch.
    private func require(_ call: String, _ result: Int32) throws {
        guard result == 0 else {
            throw ServerFailure.launchFailed("\(call): \(String(cString: strerror(result)))")
        }
    }

    /// Waits for the process group to empty after a SIGKILL: nil once it has, otherwise why it
    /// could not be confirmed gone. Members reaped by launchd stay in the group as zombies for
    /// a moment, so a single probe would name a survivor that is already dead.
    private func groupSurvivor(_ target: pid_t, within seconds: TimeInterval) -> String? {
        let deadline = Date().addingTimeInterval(seconds)
        while true {
            guard killpg(target, 0) == 0 else {
                guard errno != ESRCH else { return nil }
                return "killpg(\(target), 0) after SIGKILL: \(String(cString: strerror(errno)))"
            }
            guard Date() < deadline else {
                return "process group \(target) still had members \(seconds)s after SIGKILL"
            }
            usleep(5_000)
        }
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
    func resolve(_ event: ServerEvent, _ completion: @escaping (ServerEvent) -> Void) -> Bool {
        lock.lock()
        guard !resolved else { lock.unlock(); return false }
        resolved = true
        lock.unlock()
        DispatchQueue.main.async { completion(event) }
        return true
    }

    /// Atomically records readiness only when READY wins the startup race. Setting
    /// `becameReady` outside the same critical section would let a READY that lost to the
    /// deadline still mark the server ready, and `waitForExit` would then report an `onExit`
    /// for a server the caller was already told never started.
    func resolveReady(port: Int, _ completion: @escaping (ServerEvent) -> Void) {
        lock.lock()
        guard !resolved else { lock.unlock(); return }
        resolved = true
        becameReady = true
        lock.unlock()
        DispatchQueue.main.async { completion(.ready(port: port)) }
    }
}
