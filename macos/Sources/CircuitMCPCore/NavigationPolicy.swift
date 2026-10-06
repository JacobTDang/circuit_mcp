import Foundation

/// What the desk's navigation delegate should do with one click.
public enum NavigationDecision: Equatable {
    /// Load it in the web view.
    case allow
    /// Hand it to a download delegate. Allowing a blob download as a navigation
    /// replaces the desk with the image and saves nothing.
    case download
    /// Cancel the navigation and open an http(s) URL in the default browser.
    case openOutside
    /// Cancel the navigation and leave the desk as it was.
    case cancel
}

/// Where a click in the desk is allowed to go.
///
/// Save PNG is an `<a download>` pointing at a `blob:` URL. WKWebView only
/// treats that as a download when the policy says so; any other answer shows
/// the blob as the page. A top-level `blob:` or `data:` navigation that is
/// not a download does the same thing without even meaning to save a file,
/// so those are cancelled. A subframe may still load one: that is how an
/// image is addressed, and it does not replace the desk.
public enum NavigationPolicy {
    public static func decide(url: URL, serverRoot: URL,
                               shouldPerformDownload: Bool, isMainFrame: Bool) -> NavigationDecision {
        if shouldPerformDownload {
            return .download
        }
        let scheme = url.scheme?.lowercased() ?? ""
        let rootScheme = serverRoot.scheme?.lowercased() ?? ""
        let sameOrigin = scheme == rootScheme && url.host == serverRoot.host && url.port == serverRoot.port
        if sameOrigin || scheme == "about" {
            return .allow
        }
        if scheme == "blob" || scheme == "data" {
            return isMainFrame ? .cancel : .allow
        }
        if scheme == "http" || scheme == "https" {
            return .openOutside
        }
        return .cancel
    }
}
