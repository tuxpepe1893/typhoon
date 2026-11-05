import SwiftUI
import AppKit
import Combine
import CoreImage.CIFilterBuiltins

struct HomeKitBridgeState: Decodable, Sendable {
    let configured: Bool
    let running: Bool
    let paired: Bool
    let available: Bool
    let enabled: Bool
    let serviceLoaded: Bool
    let port: Int
    let address: String?
    let stateDirectory: String
    let lastSeen: Double?
    let pincode: String?
    let setupURI: String?
    let deviceName: String?

    enum CodingKeys: String, CodingKey {
        case configured, running, paired, available, enabled, serviceLoaded, port, address, stateDirectory, lastSeen, pincode, setupURI, device
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        configured = try values.decode(Bool.self, forKey: .configured)
        running = try values.decode(Bool.self, forKey: .running)
        paired = try values.decode(Bool.self, forKey: .paired)
        available = try values.decode(Bool.self, forKey: .available)
        enabled = try values.decode(Bool.self, forKey: .enabled)
        serviceLoaded = try values.decode(Bool.self, forKey: .serviceLoaded)
        port = try values.decode(Int.self, forKey: .port)
        address = try values.decodeIfPresent(String.self, forKey: .address)
        stateDirectory = try values.decode(String.self, forKey: .stateDirectory)
        lastSeen = try values.decodeIfPresent(Double.self, forKey: .lastSeen)
        pincode = try values.decodeIfPresent(String.self, forKey: .pincode)
        setupURI = try values.decodeIfPresent(String.self, forKey: .setupURI)
        if let device = try? values.decode(ACDevice.self, forKey: .device) {
            deviceName = device.title
        } else if let summary = try? values.decode([String: String].self, forKey: .device) {
            deviceName = summary["name"]
        } else {
            deviceName = try? values.decode(String.self, forKey: .device)
        }
    }
}

@MainActor
final class HomeKitBridgeController: ObservableObject {
    @Published private(set) var status: HomeKitBridgeState?
    @Published private(set) var busy = false
    @Published private(set) var error: String?
    @Published private(set) var pairingCode: String?
    @Published private(set) var pairingURI: String?

    func refresh() async { await request("status") }
    func enable() async { await request("enable") }
    func disable() async { hidePairing(); await request("disable") }
    func showPairing() async { await request("pairing") }
    func hidePairing() { pairingCode = nil; pairingURI = nil }

    private func request(_ action: String) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let response = try await Backend.run(["homekit", action])
            guard let status = response.homekit else { throw ControllerError.message("The bridge did not report its status.") }
            self.status = status
            if action == "pairing" {
                guard let code = status.pincode, let uri = status.setupURI else {
                    throw ControllerError.message("The bridge has not made a pairing code available. Enable it, then refresh.")
                }
                pairingCode = code; pairingURI = uri
            }
        } catch { self.error = error.localizedDescription }
    }
}

struct HomeKitBridgeView: View {
    @StateObject private var controller = HomeKitBridgeController()
    @Environment(\.scenePhase) private var scenePhase
    private let refreshTimer = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

    var body: some View {
        Form {
            Section {
                Label("Your Air Conditioner in Apple Home", systemImage: "house.fill").font(.app(.headline))
                Text("This Mac can bridge your configured air conditioner to Apple Home. Pair the bridge in Apple's Home app to use Home and Siri.")
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("Keep this Mac awake, signed in, and connected to the same network as the air conditioner. The bridge runs in the background when you sign in.")
                    .font(.app(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let status = controller.status {
                Section(controller.error == nil ? "Bridge Status" : "Last Reported Bridge Status") {
                    LabeledContent("Background service", value: status.running ? "Running" : "Stopped")
                    LabeledContent("Start at sign-in", value: status.enabled ? "Enabled" : "Disabled")
                    LabeledContent("Air conditioner", value: status.available ? "Responding" : "No recent verified read")
                    LabeledContent("Apple Home", value: status.paired ? "Paired" : "Not paired")
                    if let name = status.deviceName { LabeledContent("Device", value: name) }
                    if let lastSeen = status.lastSeen {
                        LabeledContent("Last device read", value: Date(timeIntervalSince1970: lastSeen).formatted(date: .abbreviated, time: .standard))
                    }
                    if let address = status.address { LabeledContent("Bridge address", value: "\(address):\(status.port)") }
                    HStack {
                        if !status.running || !status.enabled {
                            Button("Enable Bridge") { Task { await controller.enable() } }
                                .modifier(PrimaryControlStyle()).disabled(controller.busy)
                                .accessibilityIdentifier("enableHomeKitBridge")
                        }
                        if status.enabled || status.running {
                            Button("Disable Bridge", role: .destructive) { Task { await controller.disable() } }
                                .disabled(controller.busy).accessibilityIdentifier("disableHomeKitBridge")
                        }
                    }
                }
                Section("Pair With Apple Home") {
                    Text("On your iPhone or iPad, open Apple's Home app and choose Add Accessory. Scan the bridge's QR code here or enter its setup code in Apple's pairing screen.")
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    if let code = controller.pairingCode, let uri = controller.pairingURI {
                        VStack(spacing: 14) {
                            if let qr = pairingImage(uri) {
                                Image(nsImage: qr).interpolation(.none).resizable().scaledToFit()
                                    .frame(width: 200, height: 200).padding(14).background(.white)
                                    .accessibilityLabel("Apple Home pairing QR code")
                            }
                            Text(code).font(.app(.title2)).textSelection(.enabled)
                                .accessibilityLabel("Apple Home setup code \(code)")
                            Button("Hide Pairing Code") { controller.hidePairing() }
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 8)
                    } else {
                        Button("Show Pairing Code") { Task { await controller.showPairing() } }
                            .disabled(controller.busy || !status.configured)
                            .accessibilityIdentifier("showHomeKitPairingCode")
                    }
                    if status.paired { Text("This bridge already has a Home pairing. Its pairing state is preserved when you disable or restart it.").font(.app(.callout)).foregroundStyle(.secondary) }
                }
            } else if !controller.busy, controller.error == nil {
                Section { Text("Refresh to read the bridge's current status.").foregroundStyle(.secondary) }
            }
            if controller.busy { Section { ProgressView("Reading bridge status…") } }
            if let error = controller.error {
                Section("Bridge Needs Attention") {
                    Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .formStyle(.grouped).navigationTitle("Apple Home")
        .toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await controller.refresh() } }
                .disabled(controller.busy).accessibilityIdentifier("refreshHomeKitBridge")
        }
        .task { await controller.refresh() }
        .onReceive(refreshTimer) { _ in
            guard scenePhase == .active, controller.pairingCode == nil else { return }
            Task { await controller.refresh() }
        }
        .onDisappear { controller.hidePairing() }
    }

    private func pairingImage(_ payload: String) -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(payload.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
              let image = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return NSImage(cgImage: image, size: NSSize(width: image.width, height: image.height))
    }
}
