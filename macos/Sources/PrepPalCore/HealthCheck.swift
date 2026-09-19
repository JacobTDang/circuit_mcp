import Foundation

public enum HealthCheckError: Error, Equatable {
    case notHealthy(lastProblem: String)
}

private enum FetchDeadlineError: Error {
    case reached
}

private final class FetchDeadlineGate: @unchecked Sendable {
    private let lock = NSLock()
    private var finished = false
    private var task: Task<Void, Never>?

    func claim() -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard !finished else { return false }
        finished = true
        return true
    }

    func install(_ task: Task<Void, Never>) {
        lock.lock()
        self.task = task
        let shouldCancel = finished
        lock.unlock()
        if shouldCancel { task.cancel() }
    }

    func cancel() {
        lock.lock()
        let task = task
        lock.unlock()
        task?.cancel()
    }
}

/// Polls `GET /api/status` until it answers HTTP 200 with `"ok": true`.
public struct HealthCheck {
    public typealias Fetch = (URL) async throws -> (Data, URLResponse)

    public let timeout: TimeInterval
    public let interval: TimeInterval
    private let fetch: Fetch

    public init(timeout: TimeInterval = 30, interval: TimeInterval = 0.5,
                fetch: @escaping Fetch = { try await URLSession.shared.data(from: $0) }) {
        self.timeout = timeout
        self.interval = interval
        self.fetch = fetch
    }

    public func waitUntilHealthy(port: Int) async throws {
        let url = URL(string: "http://127.0.0.1:\(port)/api/status")!
        let deadline = Date().addingTimeInterval(timeout)
        var lastProblem = "no response yet"
        repeat {
            do {
                let (data, response) = try await fetch(url, before: deadline)
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                if status != 200 {
                    lastProblem = "HTTP \(status)"
                } else if let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          body["ok"] as? Bool == true {
                    return
                } else {
                    lastProblem = "status did not report ok: true"
                }
            } catch FetchDeadlineError.reached {
                lastProblem = "request timed out"
                break
            } catch {
                lastProblem = error.localizedDescription
            }
            let remaining = deadline.timeIntervalSinceNow
            if remaining > 0 {
                try await Task.sleep(nanoseconds: UInt64(min(interval, remaining) * 1_000_000_000))
            }
        } while Date() < deadline
        throw HealthCheckError.notHealthy(lastProblem: lastProblem)
    }

    private func fetch(_ url: URL, before deadline: Date) async throws -> (Data, URLResponse) {
        try await withCheckedThrowingContinuation { continuation in
            let gate = FetchDeadlineGate()
            let task = Task {
                do {
                    let result = try await fetch(url)
                    if gate.claim() { continuation.resume(returning: result) }
                } catch {
                    if gate.claim() { continuation.resume(throwing: error) }
                }
            }
            gate.install(task)
            DispatchQueue.global().asyncAfter(deadline: .now() + max(0, deadline.timeIntervalSinceNow)) {
                if gate.claim() {
                    gate.cancel()
                    continuation.resume(throwing: FetchDeadlineError.reached)
                }
            }
        }
    }
}
