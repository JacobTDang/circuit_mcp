import Darwin
import Foundation

/// What `ClaudeRegistrationController.start` did. Only `.started` is followed by a completion.
public enum ClaudeRegistrationStart: Equatable {
    case started
    case alreadyRunning
    case launchFailed(String)
}

public enum ClaudeRegistrationResult: Equatable {
    case succeeded(output: String)
    case failed(status: Int32, output: String)
    case timedOut(seconds: TimeInterval, output: String)
    case cancelled(output: String)

    public var message: RegistrationMessage {
        switch self {
        case .succeeded(let output):
            return RegistrationMessage(
                title: "Circuit MCP is registered with Claude Code.",
                detail: output.isEmpty ? "Claude Code completed the user-scope registration." : output)
        case .failed(let status, let output):
            return RegistrationMessage(
                title: "Claude Code registration failed (exit status \(status)).",
                detail: output.isEmpty ? "The command produced no output. Copy the MCP command and run it in Terminal for more detail." : output)
        case .timedOut(let seconds, let output):
            let detail = "Claude Code did not finish within \(Self.seconds(seconds)) seconds, so it was stopped. Try again, or copy the MCP command and run it in Terminal."
            return RegistrationMessage(title: "Claude Code registration timed out.",
                                       detail: Self.withOutput(detail, output))
        case .cancelled(let output):
            return RegistrationMessage(
                title: "Claude Code registration was cancelled.",
                detail: Self.withOutput("The Claude Code process was stopped.", output))
        }
    }

    private static func seconds(_ value: TimeInterval) -> String {
        value.rounded() == value ? String(Int(value)) : String(value)
    }

    private static func withOutput(_ detail: String, _ output: String) -> String {
        output.isEmpty ? detail : "\(detail)\n\n\(output)"
    }
}

/// Owns one Claude Code registration process from launch through reap. The timeout is injected so
/// tests and callers can choose their bound; production uses the documented 30-second default.
public final class ClaudeRegistrationController {
    public static let defaultTimeout: TimeInterval = 30

    private enum StopReason { case timeout, cancelled }

    private let timeout: TimeInterval
    private let terminationGrace: TimeInterval
    private let makeProcess: () -> Process
    private let lock = NSLock()
    private let outputQueue = DispatchQueue(label: "io.github.jacobtdang.circuitmcp.registration-output")
    private var process: Process?
    private var pipe: Pipe?
    private var output = Data()
    private var completion: ((ClaudeRegistrationResult) -> Void)?
    private var timeoutWork: DispatchWorkItem?
    private var stopReason: StopReason?

    public init(timeout: TimeInterval = ClaudeRegistrationController.defaultTimeout,
                terminationGrace: TimeInterval = 1,
                makeProcess: @escaping () -> Process = { Process() }) {
        self.timeout = timeout
        self.terminationGrace = terminationGrace
        self.makeProcess = makeProcess
    }

    public var isRunning: Bool {
        lock.lock(); defer { lock.unlock() }
        return process != nil
    }

    public var pid: pid_t? {
        lock.lock(); defer { lock.unlock() }
        guard let process, process.isRunning else { return nil }
        return process.processIdentifier
    }

    /// Launches the registration. `completion` is called exactly once, on a background queue, and
    /// only when this returns `.started`; it can run before `start` returns if the child exits at
    /// once. `.alreadyRunning` and `.launchFailed` never call it.
    @discardableResult
    public func start(executable: URL, arguments: [String],
                      completion: @escaping (ClaudeRegistrationResult) -> Void) -> ClaudeRegistrationStart {
        lock.lock()
        guard process == nil else { lock.unlock(); return .alreadyRunning }
        let child = makeProcess()
        let outputPipe = Pipe()
        child.executableURL = executable
        child.arguments = arguments
        child.standardOutput = outputPipe
        child.standardError = outputPipe
        process = child
        pipe = outputPipe
        output = Data()
        self.completion = completion
        stopReason = nil
        lock.unlock()

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self, weak child] handle in
            let data = handle.availableData
            guard !data.isEmpty, let child else { return }
            self?.outputQueue.async { self?.append(data, from: child) }
        }
        child.terminationHandler = { [weak self] terminated in self?.finished(terminated) }

        do {
            try child.run()
        } catch {
            outputPipe.fileHandleForReading.readabilityHandler = nil
            child.terminationHandler = nil
            lock.lock()
            if process === child {
                process = nil
                pipe = nil
                self.completion = nil
            }
            lock.unlock()
            return .launchFailed(error.localizedDescription)
        }

        let work = DispatchWorkItem { [weak self, weak child] in
            guard let self, let child else { return }
            _ = self.requestStop(child, reason: .timeout, wait: false)
        }
        lock.lock()
        if process === child { timeoutWork = work } else { work.cancel() }
        lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: work)
        return .started
    }

    @discardableResult
    public func cancel() -> Bool {
        lock.lock()
        let child = process
        lock.unlock()
        guard let child else { return false }
        return requestStop(child, reason: .cancelled, wait: false)
    }

    public func cancelAndWait() {
        lock.lock()
        let child = process
        lock.unlock()
        guard let child else { return }
        if !requestStop(child, reason: .cancelled, wait: true) {
            child.waitUntilExit()
        }
    }

    private func append(_ data: Data, from child: Process) {
        lock.lock(); defer { lock.unlock() }
        guard process === child else { return }
        output.append(data)
    }

    private func requestStop(_ child: Process, reason: StopReason, wait: Bool) -> Bool {
        lock.lock()
        guard process === child, stopReason == nil else { lock.unlock(); return false }
        stopReason = reason
        lock.unlock()

        if wait {
            terminateAndReap(child)
        } else {
            DispatchQueue.global().async { [weak self] in self?.terminateAndReap(child) }
        }
        return true
    }

    private func terminateAndReap(_ child: Process) {
        if child.isRunning { child.terminate() }
        let deadline = Date().addingTimeInterval(terminationGrace)
        while child.isRunning && Date() < deadline { usleep(10_000) }
        if child.isRunning { _ = kill(child.processIdentifier, SIGKILL) }
        child.waitUntilExit()
    }

    private func finished(_ child: Process) {
        lock.lock()
        guard process === child, let completion else { lock.unlock(); return }
        let outputPipe = pipe
        timeoutWork?.cancel()
        timeoutWork = nil
        lock.unlock()

        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputQueue.sync {}

        lock.lock()
        guard process === child else { lock.unlock(); return }
        let text = String(decoding: output, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let reason = stopReason
        process = nil
        pipe = nil
        self.completion = nil
        stopReason = nil
        lock.unlock()

        let result: ClaudeRegistrationResult
        switch reason {
        case .timeout: result = .timedOut(seconds: timeout, output: text)
        case .cancelled: result = .cancelled(output: text)
        case nil:
            result = child.terminationStatus == 0
                ? .succeeded(output: text)
                : .failed(status: child.terminationStatus, output: text)
        }
        completion(result)
    }
}

// @unchecked is sound because every stored `var` above is read and written only while holding `lock`.
extension ClaudeRegistrationController: @unchecked Sendable {}
