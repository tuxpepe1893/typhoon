import SwiftUI
import AppKit
import Combine

enum Palette {
    static let background = Color(nsColor: .windowBackgroundColor)
    static let panel = Color(nsColor: .controlBackgroundColor)
    static let blue = Color.accentColor
    static let secondary = Color.secondary
}

@main
struct TyphoonApp: App {
    @NSApplicationDelegateAdaptor(AppLifecycle.self) private var lifecycle
    private var controller: ClimateController { lifecycle.controller }
    var body: some Scene {
        WindowGroup("Typhoon", id: "main") {
            ContentView(controller: controller)
        }
        .defaultSize(width: 880, height: 740)
        .windowResizability(.contentMinSize)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandMenu("Air Conditioner") {
                Button("Refresh State") { Task { await controller.refresh() } }
                    .keyboardShortcut("r").disabled(controller.busy || controller.device == nil)
                Button("Connection Settings…") { controller.setupPresented = true }
                    .keyboardShortcut(",").disabled(controller.busy)
            }
        }
        MenuBarExtra("Typhoon", systemImage: "wind") { MenuBarController(controller: controller).font(.app(.body)) }
    }
}

struct ContentView: View {
    @ObservedObject var controller: ClimateController
    @State private var selection: String? = "climate"

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                // Sidebar rows override the environment font, so set it on the title text.
                sidebarRow("Air Conditioner", systemImage: "air.conditioner.horizontal").tag("climate")
                sidebarRow("Apple Home", systemImage: "house").tag("home")
            }
            .navigationTitle("Typhoon")
            // David Mono is wider than the system font; keep the footer button on one line.
            .navigationSplitViewColumnWidth(min: 220, ideal: 250, max: 300)
            .safeAreaInset(edge: .bottom) {
                Button("Connection Settings…", systemImage: "slider.horizontal.3") { controller.setupPresented = true }
                    .buttonStyle(.borderless).disabled(controller.busy).padding(16)
                    .accessibilityIdentifier("connectionSettings")
            }
        } detail: {
            if selection == "home" {
                HomeKitBridgeView()
            } else {
            VStack(spacing: 0) {
                Form {
                    if let state = controller.state {
                        Section {
                            thermostat(state)
                        }
                        Section("Climate") {
                            Picker("Mode", selection: Binding(get: { state.mode }, set: { value in Task { await controller.set("mode", value) } })) {
                                Label("Auto", systemImage: "sparkles").tag("auto")
                                Label("Cool", systemImage: "snowflake").tag("cool")
                                Label("Dry", systemImage: "drop").tag("dry")
                                Label("Heat", systemImage: "sun.max").tag("heat")
                                Label("Fan", systemImage: "fan").tag("fan")
                            }
                            .pickerStyle(.segmented).disabled(!controller.canControl).accessibilityIdentifier("modePicker")
                            Picker("Fan speed", selection: Binding(get: { state.fan }, set: { value in Task { await controller.set("fan", value) } })) {
                                Text("Auto").tag("auto")
                                Text("Low").tag("low")
                                Text("Medium").tag("medium")
                                Text("High").tag("high")
                                if !["auto", "low", "medium", "high"].contains(state.fan) {
                                    Text(state.fan.capitalized).tag(state.fan).disabled(true)
                                }
                            }
                            .pickerStyle(.segmented).disabled(!controller.canControl).accessibilityIdentifier("fanSpeed")
                            featureToggle("Swing", value: state.swing, key: "swing")
                            featureToggle("Unit display", value: state.display, key: "display")
                            if let ambient = state.ambientTemperature {
                                LabeledContent("Room temperature", value: "\(degrees(ambient)) °C")
                            } else {
                                LabeledContent("Room temperature", value: "Not reported")
                            }
                        }
                    } else {
                        Section {
                            ContentUnavailableView {
                                Label("Connect Your Air Conditioner", systemImage: "air.conditioner.horizontal")
                            } description: {
                                Text("Use your existing Wi-Fi pairing or Tornado account to control temperature, power, and airflow.")
                            } actions: {
                                Button("Connect") { controller.setupPresented = true }
                                    .modifier(PrimaryControlStyle()).disabled(controller.busy)
                            }
                        }
                    }
                    if let error = controller.error {
                        Section("Connection Needs Attention") {
                            Label(error, systemImage: "exclamationmark.circle")
                                .foregroundStyle(.secondary).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            Button("Refresh") { Task { await controller.refresh() } }
                                .disabled(controller.busy || controller.device == nil)
                        }
                    } else if let confirmation = controller.confirmation, !controller.busy {
                        Section { Label(confirmation, systemImage: "checkmark.circle").foregroundStyle(.secondary) }
                    }
                }
                .formStyle(.grouped)
                footer
            }
            .navigationTitle(controller.device?.title ?? "Air Conditioner")
            .toolbar {
                ToolbarItemGroup {
                    Button("Refresh", systemImage: "arrow.clockwise") { Task { await controller.refresh() } }
                        .disabled(controller.busy || controller.device == nil)
                        .help("Refresh live state (⌘R)").accessibilityIdentifier("refreshState")
                    Button("Connection Settings", systemImage: "slider.horizontal.3") { controller.setupPresented = true }
                        .disabled(controller.busy).help("Connection settings")
                }
            }
            }
        }
        .navigationSplitViewStyle(.balanced)
        .frame(minWidth: 720, minHeight: 600)
        .font(.app(.body))
        .sheet(isPresented: $controller.setupPresented) { SetupView(controller: controller) }
    }

    private func sidebarRow(_ title: String, systemImage: String) -> some View {
        Label { Text(title).font(.app(.body)) } icon: { Image(systemName: systemImage) }
    }

    private func thermostat(_ state: ACState) -> some View {
        VStack(spacing: 16) {
            Label(controller.connected ? (controller.usesCloud ? "Connected through Tornado cloud" : "Connected over Wi-Fi") : "Last reported settings",
                  systemImage: controller.usesCloud ? "cloud" : "wifi")
                .font(.app(.subheadline)).foregroundStyle(.secondary)
            Text("\(degrees(state.temperature))°")
                .font(.app(size: 76, weight: .light)).monospacedDigit()
                .accessibilityLabel("Set temperature \(degrees(state.temperature)) degrees Celsius")
            Text(state.power ? modeTitle(state.mode) : "Powered off")
                .font(.app(.headline)).foregroundStyle(.secondary)
            Stepper("Set temperature", value: Binding(get: { state.temperature }, set: { value in Task { await controller.set("temperature", String(value)) } }), in: 16...32, step: 0.5)
                .frame(maxWidth: 260).disabled(!controller.canControl)
                .accessibilityValue("\(degrees(state.temperature)) degrees Celsius")
                .accessibilityIdentifier("temperatureStepper")
            Button {
                Task { await controller.set("power", state.power ? "off" : "on") }
            } label: {
                Label(state.power ? "Turn Off" : "Turn On", systemImage: "power")
                    .frame(minWidth: 120)
            }
            .controlSize(.large).modifier(PrimaryControlStyle())
            .disabled(!controller.canControl).accessibilityIdentifier("powerToggle")
        }
        .frame(maxWidth: .infinity).padding(.vertical, 12)
    }

    @ViewBuilder private func featureToggle(_ title: String, value: Bool?, key: String) -> some View {
        if let value {
            Toggle(title, isOn: Binding(get: { value }, set: { newValue in Task { await controller.set(key, newValue ? "on" : "off") } }))
                .toggleStyle(.switch).disabled(!controller.canControl)
                .accessibilityIdentifier(key + "Toggle").accessibilityLabel(title)
                .accessibilityValue(value ? "On" : "Off")
        } else {
            LabeledContent(title, value: "Not reported")
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if controller.busy { ProgressView().controlSize(.small) }
            else { Image(systemName: controller.connected ? "checkmark.circle" : "circle").foregroundStyle(.secondary) }
            Text(controller.activity).font(.app(.caption)).foregroundStyle(.secondary)
            Spacer(minLength: 12)
            if let read = controller.lastRead {
                Text("Read \(read.formatted(date: .omitted, time: .standard))").font(.app(.caption)).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.bar)
    }
}

struct PrimaryControlStyle: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @ViewBuilder func body(content: Content) -> some View {
        if #available(macOS 26.0, *), !reduceTransparency {
            content.buttonStyle(.glassProminent)
        } else {
            content.buttonStyle(.borderedProminent)
        }
    }
}

struct MenuBarController: View {
    @ObservedObject var controller: ClimateController
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Text(controller.device?.title ?? "Typhoon")
        if let state = controller.state {
            Text("\(degrees(state.temperature))°C · \(state.power ? modeTitle(state.mode) : "Off")")
            Button(state.power ? "Turn Off" : "Turn On") { Task { await controller.set("power", state.power ? "off" : "on") } }
                .disabled(!controller.canControl)
        }
        Text(controller.activity)
        Divider()
        Button("Open Typhoon") { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
        Button("Refresh State") { Task { await controller.refresh() } }.disabled(controller.busy || controller.device == nil)
        Divider()
        Button("Quit Typhoon") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}

private func degrees(_ value: Double) -> String {
    value == value.rounded() ? String(Int(value)) : String(format: "%.1f", value)
}

private func modeTitle(_ mode: String) -> String {
    switch mode {
    case "cool": return "Cooling"
    case "heat": return "Heating"
    case "dry": return "Dehumidifying"
    case "fan": return "Fan only"
    case "auto": return "Auto mode"
    default: return mode.capitalized
    }
}
