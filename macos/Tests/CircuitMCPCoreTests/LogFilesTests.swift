import XCTest
@testable import CircuitMCPCore

final class LogFilesTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func testEachLaunchGetsItsOwnLogAndOnlyTheNewestFiveAreKept() throws {
        let logs = LogFiles(directory: directory)
        var created: [URL] = []
        for second in 0..<7 {
            created.append(try logs.startNewLog(now: Date(timeIntervalSince1970: 1_800_000_000 + Double(second))))
        }
        let kept = try logs.existingLogs()
        XCTAssertEqual(kept.map(\.lastPathComponent), created.suffix(5).map(\.lastPathComponent))
        XCTAssertTrue(kept.allSatisfy { $0.lastPathComponent.hasPrefix("server-") && $0.pathExtension == "log" })
    }

    func testTailReturnsTheLastFortyLines() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-x.log")
        try (1...100).map { "line \($0)" }.joined(separator: "\n").appending("\n").write(to: log, atomically: true, encoding: .utf8)
        let tail = try LogFiles.tail(of: log)
        let lines = tail.split(separator: "\n")
        XCTAssertEqual(lines.count, 40)
        XCTAssertEqual(lines.first, "line 61")
        XCTAssertEqual(lines.last, "line 100")
    }

    func testTailOfAShortLogIsTheWholeLog() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-y.log")
        try "only\ntwo".write(to: log, atomically: true, encoding: .utf8)
        XCTAssertEqual(try LogFiles.tail(of: log), "only\ntwo")
    }

    // MARK: - Reading the tail of a log of any size

    /// A fresh log holding "line 1" to "line N", each ending in a newline.
    private func numberedLog(_ name: String, lines: Int) throws -> URL {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent(name)
        try (1...lines).map { "line \($0)\n" }.joined().write(to: log, atomically: true, encoding: .utf8)
        return log
    }

    /// The error screen asked for 40 lines and got the whole file read, decoded and split first.
    /// What it reads has to be set by the cap, whatever the file has grown to.
    func testTailReadsOnlyTheEndOfALog() throws {
        let log = try numberedLog("server-big.log", lines: 200_000)
        let size = try XCTUnwrap(FileManager.default.attributesOfItem(atPath: log.path)[.size] as? UInt64)
        XCTAssertGreaterThan(size, 2_000_000)
        let handle = try FileHandle(forReadingFrom: log)
        defer { try? handle.close() }

        var bytesRead = 0
        let tail = try LogFiles.tail(size: size, lines: 40, maxBytes: 4096) { offset, count in
            try handle.seek(toOffset: offset)
            let data = try handle.read(upToCount: count) ?? Data()
            bytesRead += data.count
            return data
        }
        XCTAssertLessThanOrEqual(bytesRead, 4096, "the read is bounded by the cap, not by the file")
        let lines = tail.split(separator: "\n")
        XCTAssertEqual(lines.count, 40)
        XCTAssertEqual(lines.first, "line 199961")
        XCTAssertEqual(lines.last, "line 200000")
    }

    /// When the last 40 lines are longer than the cap, what is shown is whole lines from the end,
    /// never a fragment of one cut by the window.
    func testATailCutByTheCapStartsOnAWholeLine() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-wide.log")
        try (1...100).map { "line \($0) " + String(repeating: "x", count: 200) + "\n" }.joined()
            .write(to: log, atomically: true, encoding: .utf8)
        let tail = try LogFiles.tail(of: log, lines: 40, maxBytes: 4096)
        XCTAssertLessThanOrEqual(tail.utf8.count, 4096)
        let lines = tail.split(separator: "\n")
        XCTAssertGreaterThan(lines.count, 0)
        XCTAssertLessThan(lines.count, 40, "the cap, not the line count, decided where this tail starts")
        XCTAssertTrue(lines.allSatisfy { $0.hasPrefix("line ") }, "a line cut by the window was shown: \(lines.first ?? "")")
        XCTAssertTrue(lines.last?.hasPrefix("line 100 ") == true)
    }

    /// One endless line -- a progress bar without newlines, a dumped blob -- is still capped, and
    /// the screen says the line was cut rather than showing its end as if it were the start.
    func testASingleLineLongerThanTheCapIsShownCutAndSaysSo() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-oneline.log")
        try (String(repeating: "é", count: 100_000) + "THE END").write(to: log, atomically: true, encoding: .utf8)
        let tail = try LogFiles.tail(of: log, lines: 40, maxBytes: 4096)
        XCTAssertLessThanOrEqual(tail.utf8.count, 4096 + "…".utf8.count)
        XCTAssertTrue(tail.hasPrefix("…"), "a cut line has to look cut: \(tail.prefix(10))")
        XCTAssertTrue(tail.hasSuffix("THE END"))
        XCTAssertFalse(tail.contains("\u{FFFD}"), "the window started inside a character and the cut showed as garbage")
    }

    /// The acceptance case from the issue: a multi-gigabyte log, made sparse so the test costs no
    /// disk, whose last line is a known marker. Reading it the old way allocated the whole file
    /// several times over on the main thread.
    func testTheTailOfAMultiGigabyteLogIsReadFromItsEndAtOnce() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("server-huge.log")
        XCTAssertTrue(FileManager.default.createFile(atPath: log.path, contents: nil))
        let writer = try FileHandle(forWritingTo: log)
        try writer.truncate(atOffset: 4 << 30)
        try writer.seekToEnd()
        try writer.write(contentsOf: Data("\nTraceback (most recent call last):\nRuntimeError: the marker\n".utf8))
        try writer.close()

        let started = Date()
        let tail = try LogFiles.tail(of: log)
        XCTAssertLessThan(Date().timeIntervalSince(started), 1, "the error screen waits on this read")
        XCTAssertLessThanOrEqual(tail.utf8.count, LogFiles.tailBytes)
        XCTAssertTrue(tail.hasSuffix("Traceback (most recent call last):\nRuntimeError: the marker"), String(tail.suffix(80)))
    }

    // MARK: - A launch log that cannot outgrow its cap

    private func writer(_ name: String, maxBytes: Int) throws -> (LogWriter, URL) {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(name)
        XCTAssertTrue(FileManager.default.createFile(atPath: url.path, contents: nil))
        return (try LogWriter(url: url, handle: try FileHandle(forWritingTo: url), maxBytes: maxBytes), url)
    }

    private func size(of url: URL) throws -> Int {
        try XCTUnwrap(FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int)
    }

    /// Every chunk the server printed went into one file with no limit, so a server stuck printing
    /// filled the disk one launch at a time and the count-based retention never got a say.
    func testALaunchLogNeverGrowsPastItsCap() throws {
        let (log, url) = try writer("server-capped.log", maxBytes: 8192)
        var largest = 0
        for number in 1...2000 {
            try log.append(Data("output line \(number)\n".utf8))
            largest = max(largest, try size(of: url))
        }
        log.close()
        XCTAssertLessThanOrEqual(largest, 8192, "the size of the log at its largest")
    }

    /// Rotation exists for the one thing a capped log must not lose: what the server printed last,
    /// which is the traceback the error screen is there to show. What remains is the newest output,
    /// whole lines, in order, under a line that says the rest was dropped.
    func testAFullLogKeepsItsNewestOutputUnderANoteSayingWhatWasDropped() throws {
        let (log, url) = try writer("server-rotated.log", maxBytes: 8192)
        for number in 1...2000 { try log.append(Data("output line \(number)\n".utf8)) }
        try log.append(Data("Traceback: the last words\n".utf8))
        log.close()

        let lines = try String(contentsOf: url, encoding: .utf8).split(separator: "\n", omittingEmptySubsequences: false)
        XCTAssertTrue(lines[0].contains("CircuitMCP"), "the dropped output is announced: \(lines[0])")
        XCTAssertTrue(lines[0].contains("dropped"), "the dropped output is announced: \(lines[0])")
        let kept = lines.dropFirst().filter { !$0.isEmpty }
        XCTAssertEqual(kept.last, "Traceback: the last words")
        let numbers = kept.dropLast().map { line -> Int in
            XCTAssertTrue(line.hasPrefix("output line "), "a line cut by the rotation was kept: \(line)")
            return Int(line.dropFirst("output line ".count)) ?? -1
        }
        XCTAssertEqual(numbers.last, 2000)
        XCTAssertEqual(numbers, Array((numbers.first ?? 0)...2000), "the newest output is kept whole and in order")
        XCTAssertGreaterThan(numbers.count, 100, "rotation keeps half the cap, not a handful of lines")
    }

    /// A rotation writes the newest output to a sibling and renames it over the log, so a crash in
    /// the middle cannot leave a half-written log -- but it can leave that sibling. It goes when its
    /// launch's log does, or crashes would add up to the growth the cap is there to stop.
    func testRetiringALaunchLogAlsoRemovesAnUnfinishedRotation() throws {
        let logs = LogFiles(directory: directory)
        var created: [URL] = []
        for second in 0..<6 {
            created.append(try logs.startNewLog(now: Date(timeIntervalSince1970: 1_800_000_000 + Double(second))))
            if second == 0 {
                try Data("half a rotation".utf8).write(to: LogWriter.rotationFile(for: created[0]))
            }
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: LogWriter.rotationFile(for: created[0]).path))
        XCTAssertEqual(try logs.existingLogs().map(\.lastPathComponent), created.suffix(5).map(\.lastPathComponent))
    }
}
