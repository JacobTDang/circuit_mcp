import XCTest
@testable import CircuitMCPCore

/// Where a click in the desk is allowed to go. Save PNG is an `<a download>`
/// on a blob URL; allowing that navigation replaces the desk with the image.
final class NavigationPolicyTests: XCTestCase {
    private let root = URL(string: "http://127.0.0.1:2300/")!

    func testSameOriginPagesStayInTheDesk() {
        let url = URL(string: "http://127.0.0.1:2300/workspace")!
        XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                shouldPerformDownload: false, isMainFrame: true),
                       .allow)
    }

    func testAboutBlankIsPartOfTheWebView() {
        let url = URL(string: "about:blank")!
        XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                shouldPerformDownload: false, isMainFrame: true),
                       .allow)
    }

    func testADownloadIsADownloadEvenForABlobOrDataURL() {
        let blob = URL(string: "blob:http://127.0.0.1:2300/309b8d5a-db9c-43cd-8d4d-517d019f2f1d")!
        let data = URL(string: "data:image/png;base64,aaaa")!
        for url in [blob, data] {
            XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                    shouldPerformDownload: true, isMainFrame: true),
                           .download, url.absoluteString)
        }
    }

    func testABlobOrDataURLWithoutTheDownloadFlagDoesNotReplaceTheDesk() {
        let blob = URL(string: "blob:http://127.0.0.1:2300/309b8d5a-db9c-43cd-8d4d-517d019f2f1d")!
        let data = URL(string: "data:text/html,goodbye")!
        for url in [blob, data] {
            XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                    shouldPerformDownload: false, isMainFrame: true),
                           .cancel, url.absoluteString)
        }
    }

    /// An image or a script addressed by a blob or data URL loads in a subframe.
    /// Cancelling those would blank the drawings. Only the top-level desk must refuse them.
    func testABlobOrDataURLInASubframeIsStillAllowed() {
        let blob = URL(string: "blob:http://127.0.0.1:2300/309b8d5a-db9c-43cd-8d4d-517d019f2f1d")!
        let data = URL(string: "data:image/svg+xml,svg")!
        for url in [blob, data] {
            XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                    shouldPerformDownload: false, isMainFrame: false),
                           .allow, url.absoluteString)
        }
    }

    func testAnOffOriginWebLinkLeavesTheDesk() {
        let url = URL(string: "https://example.com/lab")!
        XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                shouldPerformDownload: false, isMainFrame: true),
                       .openOutside)
    }

    /// `file:` would open anything the user can read, on the page's say-so.
    func testAFileURLIsCancelledRatherThanOpened() {
        let url = URL(string: "file:///etc/passwd")!
        XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                shouldPerformDownload: false, isMainFrame: true),
                       .cancel)
    }

    func testADownloadBeatsLeavingTheDesk() {
        let url = URL(string: "https://example.com/schematic.png")!
        XCTAssertEqual(NavigationPolicy.decide(url: url, serverRoot: root,
                                                shouldPerformDownload: true, isMainFrame: true),
                       .download)
    }
}
