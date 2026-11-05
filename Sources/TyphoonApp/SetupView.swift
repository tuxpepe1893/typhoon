import SwiftUI

struct SetupView: View {
    @ObservedObject var controller: ClimateController
    @Environment(\.dismiss) private var dismiss
    @State private var route = "local"
    @State private var manual = false
    @State private var host = ""
    @State private var mac = ""
    @State private var name = "Tornado SMART-22X"
    @State private var email = ""
    @State private var password = ""
    @State private var region = "usa"

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Connect your Tornado").font(.app(size: 23, weight: .semibold))
                    Text("Use your existing Wi‑Fi pairing.").foregroundStyle(Palette.secondary)
                }
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill").font(.app(.title3)).foregroundStyle(Palette.secondary)
                }
                .buttonStyle(.plain).disabled(controller.busy).accessibilityLabel("Close connection settings")
            }
            Picker("Connection method", selection: $route) {
                Text("Local Wi‑Fi").tag("local")
                Text("Tornado account").tag("cloud")
            }
            .pickerStyle(.segmented).disabled(controller.busy)
            .accessibilityIdentifier("connectionMethod")
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if route == "local" { localSetup }
                    else { cloudSetup }
                    if let error = controller.error {
                        Label(error, systemImage: "exclamationmark.circle")
                            .font(.app(size: 12)).foregroundStyle(.orange).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollIndicators(.hidden)
            HStack(spacing: 9) {
                if controller.busy { ProgressView().controlSize(.small) }
                Text(controller.activity).font(.app(size: 11)).foregroundStyle(Palette.secondary)
            }
        }
        .padding(28).frame(width: 560, height: 660)
        .background(Palette.background).tint(Palette.blue)
        .font(.app(.body))
        .interactiveDismissDisabled(controller.busy)
        .onAppear { route = controller.usesCloud ? "cloud" : "local" }
        .onDisappear { password = "" }
    }

    private var localSetup: some View {
        VStack(alignment: .leading, spacing: 20) {
            infoCard("wifi", "Keep the air conditioner on its existing 2.4 GHz Wi‑Fi. This Mac must share the same local network; the Mac can use 5 GHz. Your Tornado WiFi 3 pairing stays in place. No password is needed for local control.")
            Button { Task { await controller.discover() } } label: {
                Label("Find air conditioner", systemImage: "magnifyingglass")
            }
            .buttonStyle(.borderedProminent).controlSize(.large).disabled(controller.busy)
            .accessibilityIdentifier("discoverDevices")
            if !controller.discovered.isEmpty {
                deviceList(controller.discovered)
            } else if controller.hasSearched, !controller.busy, controller.error == nil {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Nothing found yet").font(.app(size: 13, weight: .semibold))
                    Text("Check whether Tornado WiFi 3 sees the unit online. Allow Typhoon in System Settings → Privacy & Security → Local Network. A guest network, VPN, or router device isolation can block discovery. You can also try your Tornado account above.")
                        .font(.app(size: 12)).foregroundStyle(Palette.secondary).lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                    offlineHelp
                }
            }
            DisclosureGroup("Enter device details manually", isExpanded: $manual) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Find the IP and MAC address in your router's connected devices. Searching a known IP can fill in the rest.")
                        .font(.app(size: 11)).foregroundStyle(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack {
                        TextField("IP address", text: $host).accessibilityLabel("Device IP address")
                        Button("Find at IP") { Task { await controller.discover(host: host) } }
                            .disabled(controller.busy || host.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                    TextField("MAC address (aa:bb:cc:dd:ee:ff)", text: $mac).accessibilityLabel("Device MAC address")
                    TextField("Device name", text: $name).accessibilityLabel("Device name")
                    Button("Save and connect") {
                        Task { await controller.connect(ACDevice(host: host.trimmingCharacters(in: .whitespaces), mac: mac.trimmingCharacters(in: .whitespaces), name: name)) }
                    }
                    .disabled(controller.busy || host.trimmingCharacters(in: .whitespaces).isEmpty || mac.trimmingCharacters(in: .whitespaces).isEmpty)
                    .accessibilityIdentifier("manualConnect")
                }
                .textFieldStyle(.roundedBorder).padding(.top, 12)
            }
            .font(.app(size: 12)).foregroundStyle(Palette.secondary)
        }
    }

    private var cloudSetup: some View {
        VStack(alignment: .leading, spacing: 20) {
            infoCard("cloud", "Connect with the same account you use in Tornado WiFi 3. This option uses Tornado's internet service when local discovery cannot reach the unit. Choose USA for accounts registered in Israel.")
            VStack(alignment: .leading, spacing: 12) {
                TextField("Tornado account email", text: $email)
                    .textContentType(.username).accessibilityLabel("Tornado account email")
                    .autocorrectionDisabled()
                SecureField("Tornado account password", text: $password)
                    .textContentType(.password).accessibilityLabel("Tornado account password")
                Picker("Account region", selection: $region) {
                    Text("USA (Israel)").tag("usa")
                    Text("Europe").tag("eu")
                }
                .accessibilityIdentifier("accountRegion")
                HStack {
                    Button("Sign in") {
                        let submittedPassword = password
                        password = ""
                        Task { await controller.cloudLogin(email: email, password: submittedPassword, region: region) }
                    }
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .disabled(email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || password.isEmpty)
                    .accessibilityIdentifier("cloudSignIn")
                    Button(controller.cloudSessionAvailable ? "Refresh account devices" : "Use saved session") {
                        Task { await controller.listCloudDevices() }
                    }
                    .accessibilityIdentifier("cloudSavedSession")
                }
            }
            .textFieldStyle(.roundedBorder).disabled(controller.busy)
            Label("The session is saved in macOS Keychain. Your password is sent only for sign-in and is not retained. Tornado's service is contacted when you sign in or use a saved session.", systemImage: "lock.shield")
                .font(.app(size: 11)).foregroundStyle(Palette.secondary).lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            if !controller.cloudDevices.isEmpty {
                deviceList(controller.cloudDevices)
            } else if controller.hasListedCloud, !controller.busy, controller.error == nil {
                VStack(alignment: .leading, spacing: 10) {
                    Text("No air conditioners in this account").font(.app(size: 13, weight: .semibold))
                    Text("Check the account and region match Tornado WiFi 3, and that the air conditioner appears in that app.")
                        .font(.app(size: 12)).foregroundStyle(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    offlineHelp
                }
            }
            if controller.cloudSessionAvailable {
                Button("Sign out and remove saved session", role: .destructive) {
                    password = ""
                    Task { await controller.cloudLogout() }
                }
                .font(.app(size: 11)).disabled(controller.busy)
                .accessibilityIdentifier("cloudSignOut")
            }
        }
    }

    private func infoCard(_ symbol: String, _ text: String) -> some View {
        Label { Text(text).font(.app(size: 12)).lineSpacing(4).fixedSize(horizontal: false, vertical: true) }
            icon: { Image(systemName: symbol).foregroundStyle(Palette.blue) }
            .padding(16).background(Palette.panel, in: RoundedRectangle(cornerRadius: 12))
    }

    private func deviceList(_ devices: [ACDevice]) -> some View {
        VStack(spacing: 8) {
            ForEach(devices) { device in
                HStack {
                    Image(systemName: "air.conditioner.horizontal").font(.app(.title2)).foregroundStyle(Palette.blue)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(device.title).font(.app(size: 13, weight: .medium))
                        Text(device.host).font(.app(size: 11)).foregroundStyle(Palette.secondary)
                    }
                    Spacer()
                    Button("Connect") { Task { await controller.connect(device) } }
                        .disabled(controller.busy)
                        .accessibilityLabel("Connect to \(device.title) at \(device.host)")
                }
                .padding(12).background(Palette.panel, in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private var offlineHelp: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Only if the unit is offline: reconnect it in Tornado WiFi 3 using the instructions for your remote. Pairing buttons vary by remote; the guide below shows a model with HEALTH. Enter your Wi‑Fi password only in the official phone app.")
                .font(.app(size: 11)).foregroundStyle(Palette.secondary).lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            Link("Watch Tornado's Wi‑Fi pairing guide", destination: URL(string: "https://www.youtube.com/watch?v=UggjlfWnQ0k&t=90s")!)
                .font(.app(size: 11))
        }
    }
}
