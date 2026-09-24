import AppKit

// Top-level code in main.swift runs on the main thread before any concurrency exists, which is
// what assumeIsolated states: the delegate is main-actor isolated and this is that thread.
MainActor.assumeIsolated {
    let application = NSApplication.shared
    let appDelegate = AppDelegate()
    application.delegate = appDelegate
    application.setActivationPolicy(.regular)
    application.run()
}
