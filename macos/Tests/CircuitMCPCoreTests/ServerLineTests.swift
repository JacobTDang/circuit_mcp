import XCTest
@testable import CircuitMCPCore

final class ServerLineTests: XCTestCase {
    func testReadyCarriesThePort() {
        XCTAssertEqual(ServerLine.parse("READY 54321\n"), .ready(port: 54321))
    }

    func testLockedCarriesTheHolder() {
        XCTAssertEqual(ServerLine.parse("LOCKED 812"), .locked(pid: "812"))
    }

    func testAnUnknownHolderIsStillALockNotAMalformedLine() {
        XCTAssertEqual(ServerLine.parse("LOCKED unknown"), .locked(pid: "unknown"))
    }

    func testOrdinaryLogOutputIsNotAProtocolLine() {
        XCTAssertNil(ServerLine.parse("INFO:     Started server process [1234]"))
        XCTAssertNil(ServerLine.parse(""))
    }

    func testABrokenProtocolLineIsReportedNotIgnored() {
        XCTAssertEqual(ServerLine.parse("READY"), .malformed(line: "READY"))
        XCTAssertEqual(ServerLine.parse("READY abc"), .malformed(line: "READY abc"))
        XCTAssertEqual(ServerLine.parse("READY 70000"), .malformed(line: "READY 70000"))
        XCTAssertEqual(ServerLine.parse("LOCKED"), .malformed(line: "LOCKED"))
    }

    // MARK: - The server's output as lines

    /// Feeds `data` to an `OutputLines` `size` bytes at a time, as the reader's `read` hands it over,
    /// and reports the lines and the most it ever held.
    private func split(_ data: Data, readsOf size: Int, maxLineBytes: Int) -> (lines: [String], largestHeld: Int) {
        var output = OutputLines(maxLineBytes: maxLineBytes)
        var lines: [String] = []
        var largest = 0
        var offset = 0
        while offset < data.count {
            let end = min(offset + size, data.count)
            lines += output.append(data[offset..<end])
            largest = max(largest, output.heldBytes)
            offset = end
        }
        return (lines, largest)
    }

    /// However the reads cut it up, ordinary output reaches `ServerLine` as the lines it was printed as.
    func testOrdinaryOutputComesOutAsTheLinesItWasPrintedAs() {
        let printed = ["INFO: starting", "", "Traceback (most recent call last):", "  café ☕", "READY 45678"]
        let data = Data((printed.joined(separator: "\n") + "\n").utf8)
        for size in [1, 2, 3, 7, 4096] {
            XCTAssertEqual(split(data, readsOf: size, maxLineBytes: 65_536).lines, printed, "read \(size) bytes at a time")
        }
    }

    /// The reader held a line that had not ended for as long as it went on: a server printing
    /// megabytes with no newline made the app hold megabytes, without limit. Past the bound the
    /// line comes out cut, and nothing more of it is held; the line after it is read whole.
    func testALineWithNoEndIsHeldOnlyUpToTheBoundAndComesOutCut() {
        var data = Data(repeating: UInt8(ascii: "x"), count: 5_000_000)
        data.append(Data("\nREADY 45678\n".utf8))
        let (lines, largest) = split(data, readsOf: 4096, maxLineBytes: 65_536)
        XCTAssertLessThanOrEqual(largest, 65_536, "the most of an unfinished line held at once")
        XCTAssertEqual(lines.count, 2)
        XCTAssertEqual(lines.first, String(repeating: "x", count: 65_536) + "…")
        XCTAssertEqual(lines.last, "READY 45678")
    }

    /// The server never sent the rest of a cut line, so it cannot be read as the protocol line it
    /// started as: cut at the spaces after "READY 45678", it would otherwise trim down to a READY
    /// for a port nothing checked.
    func testACutLineIsNeverReadAsAProtocolLine() {
        let data = Data(("READY 45678" + String(repeating: " ", count: 100) + "the rest\n").utf8)
        let lines = split(data, readsOf: 4096, maxLineBytes: 64).lines
        XCTAssertEqual(lines.count, 1)
        guard case .malformed? = ServerLine.parse(lines[0]) else {
            return XCTFail("a cut line was read as \(String(describing: ServerLine.parse(lines[0])))")
        }
    }

    func testALineExactlyAtTheBoundIsNotCut() {
        let line = String(repeating: "y", count: 64)
        XCTAssertEqual(split(Data((line + "\nnext\n").utf8), readsOf: 5, maxLineBytes: 64).lines, [line, "next"])
    }
}
