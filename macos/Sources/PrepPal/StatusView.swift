import AppKit

/// Covers the web view while the server starts, and explains any failure with the log tail.
final class StatusView: NSView {
    var onOpenLog: (() -> Void)?
    var onRestart: (() -> Void)?
    var onQuit: (() -> Void)?

    private let spinner = NSProgressIndicator()
    private let titleLabel = NSTextField(wrappingLabelWithString: "")
    private let detailLabel = NSTextField(wrappingLabelWithString: "")
    private let logView = NSTextView()
    private let logScroll = NSScrollView()
    private let buttonRow = NSStackView()

    init() {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        spinner.style = .spinning
        titleLabel.font = .systemFont(ofSize: 17, weight: .semibold)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.isSelectable = true

        logView.isEditable = false
        logView.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        logScroll.documentView = logView
        logScroll.hasVerticalScroller = true
        logScroll.borderType = .bezelBorder
        logView.autoresizingMask = [.width]

        let openLog = NSButton(title: "Open Log", target: self, action: #selector(openLogPressed))
        let restart = NSButton(title: "Restart", target: self, action: #selector(restartPressed))
        let quit = NSButton(title: "Quit", target: self, action: #selector(quitPressed))
        restart.keyEquivalent = "\r"
        [openLog, restart, quit].forEach(buttonRow.addArrangedSubview)

        let column = NSStackView(views: [spinner, titleLabel, detailLabel, logScroll, buttonRow])
        column.orientation = .vertical
        column.alignment = .leading
        column.spacing = 12
        column.translatesAutoresizingMaskIntoConstraints = false
        addSubview(column)
        NSLayoutConstraint.activate([
            column.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 32),
            column.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -32),
            column.centerYAnchor.constraint(equalTo: centerYAnchor),
            logScroll.widthAnchor.constraint(equalTo: column.widthAnchor),
            logScroll.heightAnchor.constraint(equalToConstant: 260),
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("StatusView is built in code")
    }

    func showLoading(_ message: String) {
        isHidden = false
        spinner.isHidden = false
        spinner.startAnimation(nil)
        titleLabel.stringValue = message
        detailLabel.isHidden = true
        logScroll.isHidden = true
        buttonRow.isHidden = true
    }

    func showError(title: String, detail: String, logTail: String?) {
        isHidden = false
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        titleLabel.stringValue = title
        detailLabel.stringValue = detail
        detailLabel.isHidden = false
        logView.string = logTail ?? ""
        logScroll.isHidden = logTail == nil
        buttonRow.isHidden = false
    }

    func hide() {
        spinner.stopAnimation(nil)
        isHidden = true
    }

    @objc private func openLogPressed() { onOpenLog?() }
    @objc private func restartPressed() { onRestart?() }
    @objc private func quitPressed() { onQuit?() }
}
