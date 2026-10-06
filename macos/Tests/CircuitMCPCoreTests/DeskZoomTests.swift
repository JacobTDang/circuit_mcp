import XCTest
@testable import CircuitMCPCore

/// WKWebView.pageZoom is a scale. The menu steps it and refuses to let a
/// stuck key shrink the desk's labels to nothing or blow them up without bound.
final class DeskZoomTests: XCTestCase {
    func testZoomInMultipliesAndZoomOutDivides() {
        XCTAssertEqual(DeskZoom.zoomIn(from: DeskZoom.actual), DeskZoom.actual * DeskZoom.step, accuracy: 1e-9)
        XCTAssertEqual(DeskZoom.zoomOut(from: DeskZoom.actual), DeskZoom.actual / DeskZoom.step, accuracy: 1e-9)
    }

    func testZoomInStopsAtTheMaximum() {
        XCTAssertEqual(DeskZoom.zoomIn(from: DeskZoom.maximum), DeskZoom.maximum)
        XCTAssertEqual(DeskZoom.zoomIn(from: DeskZoom.maximum - 0.01), DeskZoom.maximum)
    }

    func testZoomOutStopsAtTheMinimum() {
        XCTAssertEqual(DeskZoom.zoomOut(from: DeskZoom.minimum), DeskZoom.minimum)
        XCTAssertEqual(DeskZoom.zoomOut(from: DeskZoom.minimum + 0.01), DeskZoom.minimum)
    }

    func testClampPullsOutOfRangeValuesBack() {
        XCTAssertEqual(DeskZoom.clamp(0), DeskZoom.minimum)
        XCTAssertEqual(DeskZoom.clamp(100), DeskZoom.maximum)
        XCTAssertEqual(DeskZoom.clamp(DeskZoom.actual), DeskZoom.actual)
    }

    /// A corrupt preference must not be handed to the web view: NaN pageZoom
    /// leaves the desk unreadable and there is no menu item that can undo it.
    func testANonFiniteZoomBecomesActualSize() {
        XCTAssertEqual(DeskZoom.clamp(.nan), DeskZoom.actual)
        XCTAssertEqual(DeskZoom.clamp(.infinity), DeskZoom.actual)
        XCTAssertEqual(DeskZoom.zoomIn(from: .nan), DeskZoom.actual * DeskZoom.step, accuracy: 1e-9)
    }
}
