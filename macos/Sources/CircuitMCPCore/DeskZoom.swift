import Foundation

/// The desk's page scale. `WKWebView.pageZoom` is a multiplier on the whole
/// page, and the labels on a card are 9–11 px, so the menu has to be able to
/// enlarge them. The step is multiplicative so repeated Zoom In does not
/// crawl, and the bounds keep a stuck key from scaling the page until the
/// text is a single pixel or one letter fills the window.
public enum DeskZoom {
    public static let minimum = 0.5
    public static let maximum = 3.0
    public static let actual = 1.0
    public static let step = 1.2

    /// A non-finite value is a corrupt preference. Handing it to the web view
    /// leaves the desk unreadable, and no menu item can undo NaN, so it comes
    /// back as actual size.
    public static func clamp(_ zoom: Double) -> Double {
        guard zoom.isFinite else { return actual }
        return min(maximum, max(minimum, zoom))
    }

    public static func zoomIn(from zoom: Double) -> Double {
        clamp(clamp(zoom) * step)
    }

    public static func zoomOut(from zoom: Double) -> Double {
        clamp(clamp(zoom) / step)
    }
}
