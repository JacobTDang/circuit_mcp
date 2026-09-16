import XCTest
@testable import PrepPalCore

final class HealthCheckTests: XCTestCase {
    private func response(_ status: Int, _ body: String) -> (Data, URLResponse) {
        let url = URL(string: "http://127.0.0.1:1/api/status")!
        return (Data(body.utf8), HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }

    func testHealthyAfterTheServerStartsAnswering() async throws {
        var calls = 0
        let check = HealthCheck(timeout: 5, interval: 0.01) { url in
            calls += 1
            XCTAssertEqual(url.absoluteString, "http://127.0.0.1:5555/api/status")
            if calls < 3 { throw URLError(.cannotConnectToHost) }
            return self.response(200, #"{"ok": true, "tool_count": 50}"#)
        }
        try await check.waitUntilHealthy(port: 5555)
        XCTAssertEqual(calls, 3)
    }

    func testTimesOutNamingTheLastProblem() async {
        let check = HealthCheck(timeout: 0.2, interval: 0.05) { _ in self.response(500, #"{"detail": "boom"}"#) }
        do {
            try await check.waitUntilHealthy(port: 5556)
            XCTFail("expected a timeout")
        } catch HealthCheckError.notHealthy(let lastProblem) {
            XCTAssertEqual(lastProblem, "HTTP 500")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testOkFalseIsNotHealthy() async {
        let check = HealthCheck(timeout: 0.2, interval: 0.05) { _ in self.response(200, #"{"ok": false}"#) }
        do {
            try await check.waitUntilHealthy(port: 5557)
            XCTFail("expected a timeout")
        } catch HealthCheckError.notHealthy(let lastProblem) {
            XCTAssertEqual(lastProblem, "status did not report ok: true")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testAStalledFetchCannotOverrunTheOverallTimeout() async {
        let started = Date()
        let check = HealthCheck(timeout: 0.1, interval: 0.01) { _ in
            try await Task.sleep(nanoseconds: 5_000_000_000)
            return self.response(200, #"{"ok": true}"#)
        }
        do {
            try await check.waitUntilHealthy(port: 5558)
            XCTFail("expected a timeout")
        } catch HealthCheckError.notHealthy {
            XCTAssertLessThan(Date().timeIntervalSince(started), 0.5)
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }
}
