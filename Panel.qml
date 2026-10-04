import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import Quickshell.Wayland
import qs.Commons

Item {
  id: root
  property var shell: null
  property var manifest: null
  property var current: []
  property var saved: []
  property var currentWorkspaces: []
  property var savedWorkspaces: []
  property var savedAt: null
  property string lastView: "current"
  property var setups: []
  property var selected: null
  property string selectedName: ""
  property string error: ""
  property string notice: ""
  property string startStderr: ""
  readonly property string script: Qt.resolvedUrl("bin/omarchinator").toString().replace(/^file:\/\//, "")
  readonly property string pluginId: (manifest && manifest.id) || "io.github.hjanuschka.omarchinator"
  readonly property color muted: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.62)
  readonly property color surface: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.08)
  readonly property bool busy: preview.running || listProcess.running || saveProcess.running || startProcess.running || urlProcess.running
  readonly property var displayedEntries: selectedName ? (selected ? selected.entries : [])
    : (lastView === "saved" ? saved : current)
  readonly property var displayedWorkspaces: selectedName ? (selected ? selected.workspaces : [])
    : (lastView === "saved" ? savedWorkspaces : currentWorkspaces)
  readonly property var browserEntries: selected && selected.entries
    ? selected.entries.map(function(entry, index) { return { entry: entry, index: index } })
      .filter(function(item) { return item.entry.browser }) : []
  readonly property int blankBrowserCount: browserEntries.filter(function(item) { return !item.entry.url }).length

  function open(payloadJson) {
    window.visible = true
    refresh()
    if (payloadJson) {
      try {
        var payload = JSON.parse(String(payloadJson))
        if (payload.setup) selectSetup(String(payload.setup))
      } catch (e) { error = "Invalid setup request" }
    }
  }
  function close() { window.visible = false }
  function dismiss() {
    if (shell && typeof shell.hide === "function") shell.hide(pluginId)
    else close()
  }
  function refreshLive() {
    Hyprland.refreshWorkspaces()
    Hyprland.refreshToplevels()
    if (!preview.running) preview.running = true
  }
  function refresh() {
    notice = "Refreshing window list..."
    refreshLive()
    refreshSetups()
  }
  function refreshSetups() {
    if (!listProcess.running) listProcess.running = true
  }
  function selectSetup(name) {
    selectedName = name
    selected = null
    if (name && !detailsProcess.running) {
      detailsProcess.command = [script, "setup", "show", name]
      detailsProcess.running = true
    }
  }
  function saveLast() {
    if (busy) return
    error = ""
    saveProcess.command = [script, "save"]
    saveProcess.running = true
  }
  function saveAs() {
    if (busy) return
    var name = nameField.text.trim()
    if (!name) { error = "Enter a setup name"; return }
    error = ""
    notice = "Capturing visible monitor..."
    saveProcess.command = [script, "setup", "save", name, "--screenshot"]
    window.visible = false
    captureDelay.start()
  }
  function startSetup() {
    if (!selectedName || busy) return
    error = ""
    notice = "Starting " + selectedName + "..."
    startStderr = ""
    startProcess.command = [script, "setup", "start", selectedName]
    startProcess.running = true
  }
  function applyLast() {
    if (busy || !saved.length) return
    error = ""
    notice = "Applying Last..."
    startStderr = ""
    startProcess.command = [script, "apply"]
    startProcess.running = true
  }
  function setUrl(index, url) {
    if (busy) return
    error = ""
    urlProcess.command = [script, "setup", "url", selectedName, String(index), url.trim()]
    urlProcess.running = true
  }

  Timer { id: captureDelay; interval: 400; onTriggered: saveProcess.running = true }

  Process {
    id: preview
    command: [root.script, "preview"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text)
          root.current = data.current || []
          root.saved = data.saved || []
          root.currentWorkspaces = data.current_workspaces || []
          root.savedWorkspaces = data.saved_workspaces || []
          root.savedAt = data.saved_at
        } catch (e) { root.error = "Could not read last session: " + e }
      }
    }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim()) root.error = text.trim() }
  }
  Process {
    id: listProcess
    command: [root.script, "setup", "list"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          root.setups = JSON.parse(text)
          if (root.selectedName && !root.setups.some(function(s) { return s.name === root.selectedName }))
            root.selectedName = ""
          if (root.selectedName) root.selectSetup(root.selectedName)
        } catch (e) { root.error = "Could not list setups: " + e }
      }
    }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim()) root.error = text.trim() }
  }
  Process {
    id: detailsProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text)
          if (data.name === root.selectedName) root.selected = data
        } catch (e) { root.error = "Could not read setup: " + e }
      }
    }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim()) root.error = text.trim() }
    onExited: if (root.selectedName && detailsProcess.command[3] !== root.selectedName)
      root.selectSetup(root.selectedName)
  }
  Process {
    id: saveProcess
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.notice = text.trim().startsWith("{") ? "Setup saved" : text.trim() }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim()) root.error = text.trim() }
    onExited: function(code) {
      window.visible = true
      if (code === 0 && saveProcess.command[1] === "setup") root.selectedName = nameField.text.trim()
      root.refresh()
    }
  }
  Process {
    id: startProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          root.notice = result.matched + " matched, " + result.launched + " launched"
          if (result.missing.length)
            root.error = result.missing.length + " windows did not open"
          else if (result.blank_chrome.length)
            root.error = result.blank_chrome.length + " Chrome windows opened blank; original tabs were not restored"
        } catch (e) { root.error = "Could not read start result: " + e }
      }
    }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.startStderr = text.trim() }
    onExited: function(code) {
      if (code !== 0) root.error = root.startStderr || "Setup could not be applied"
      root.refreshLive()
    }
  }
  Process {
    id: urlProcess
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.notice = "Chrome URL updated" }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim()) root.error = text.trim() }
    onExited: root.selectSetup(root.selectedName)
  }

  PanelWindow {
    id: window
    visible: false
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchinator"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: Qt.rgba(0, 0, 0, 0.75)
      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }
    }
    Rectangle {
      anchors.centerIn: parent
      width: Math.min(parent.width - 32, 1180)
      height: Math.min(parent.height - 32, 820)
      radius: 16
      color: Color.background
      border.color: root.surface
      MouseArea { anchors.fill: parent; onClicked: {} }

      FocusScope {
        anchors.fill: parent
        focus: true
        Keys.onEscapePressed: root.dismiss()
        ColumnLayout {
          anchors.fill: parent
          anchors.margins: 24
          spacing: 16

          RowLayout {
            Layout.fillWidth: true
            Text { text: "OMARCHINATOR"; color: Color.accent; font.pixelSize: 23; font.bold: true; font.letterSpacing: 2 }
            Item { Layout.fillWidth: true }
            ActionButton { label: "Refresh"; onClicked: root.refresh() }
            ActionButton { label: "Close"; onClicked: root.dismiss() }
          }
          Text {
            text: root.error || root.notice || "Your last session recovers after a reboot. Named setups are reusable workspaces."
            color: root.error ? Color.urgent : root.muted
            elide: Text.ElideRight
            Layout.fillWidth: true
          }
          RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 18

            ColumnLayout {
              Layout.preferredWidth: 250
              Layout.fillHeight: true
              spacing: 8
              Text { text: "SESSIONS"; color: root.muted; font.bold: true; font.pixelSize: 11 }
              ActionButton {
                label: "Last  -  autosaved"
                active: root.selectedName === ""
                Layout.fillWidth: true
                onClicked: { root.selectedName = ""; root.selected = null }
              }
              Text { text: "SETUPS"; color: root.muted; font.bold: true; font.pixelSize: 11; Layout.topMargin: 16 }
              ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ColumnLayout {
                  width: parent.width
                  Repeater {
                    model: root.setups
                    delegate: Rectangle {
                      required property var modelData
                      Layout.fillWidth: true
                      implicitHeight: 62
                      radius: 8
                      color: root.selectedName === modelData.name
                        ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.22) : root.surface
                      RowLayout {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 8
                        Image {
                          visible: !!modelData.preview
                          source: modelData.preview ? "file://" + modelData.preview : ""
                          Layout.preferredWidth: visible ? 64 : 0
                          Layout.preferredHeight: 48
                          fillMode: Image.PreserveAspectCrop
                          cache: false
                          clip: true
                        }
                        ColumnLayout {
                          Layout.fillWidth: true
                          Text { text: modelData.name; color: Color.foreground; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                          Text { text: modelData.windows + " windows"; color: root.muted; font.pixelSize: 11 }
                        }
                      }
                      MouseArea { anchors.fill: parent; onClicked: root.selectSetup(modelData.name) }
                    }
                  }
                }
              }
              Text { text: "SAVE AS SETUP"; color: root.muted; font.bold: true; font.pixelSize: 11 }
              TextField {
                id: nameField
                Layout.fillWidth: true
                placeholderText: "e.g. Company A"
                maximumLength: 48
                onAccepted: root.saveAs()
              }
              ActionButton { label: "Save as setup"; prominent: true; enabled: !root.busy; Layout.fillWidth: true; onClicked: root.saveAs() }
              Text {
                text: "Setups save screenshots. Open windows preview live without disk captures."
                color: root.muted
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                font.pixelSize: 11
              }
            }

            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: root.surface }

            ScrollView {
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
              ColumnLayout {
                width: parent.width
                spacing: 12

                Text {
                  text: root.selectedName || "Last session"
                  color: Color.foreground
                  font.pixelSize: 24
                  font.bold: true
                }
                Text {
                  text: root.selectedName
                    ? "Reusable setup  -  " + (root.selected ? root.selected.windows : "...") + " windows"
                    : root.lastView === "current" ? "Live desktop  -  " + root.current.length + " windows"
                    : "Automatic recovery  -  " + root.saved.length + " saved windows"
                  color: root.muted
                }
                RowLayout {
                  visible: !!root.selectedName
                  ActionButton {
                    label: "Apply setup"
                    prominent: true
                    enabled: !root.busy && !!root.selected
                    onClicked: root.startSetup()
                  }
                  Text { text: "Adds and arranges windows; never closes apps"; color: root.muted; font.pixelSize: 11 }
                }
                Text {
                  visible: !!root.selectedName && root.blankBrowserCount > 0
                  text: root.blankBrowserCount + " Chrome window(s) have no URL. They will open blank on their saved workspaces; add URLs to reopen pages:"
                  color: Color.urgent
                  wrapMode: Text.WordWrap
                  Layout.fillWidth: true
                }
                Repeater {
                  model: root.selectedName ? root.browserEntries : []
                  delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Text {
                      text: "WS " + modelData.entry.workspace + " / " + modelData.entry.title
                      color: Color.foreground
                      elide: Text.ElideRight
                      Layout.preferredWidth: 230
                    }
                    TextField {
                      id: browserUrl
                      Layout.fillWidth: true
                      text: modelData.entry.url || ""
                      placeholderText: "https://..."
                      onAccepted: root.setUrl(modelData.index, text)
                    }
                    ActionButton { label: "Set URL"; enabled: !root.busy; onClicked: root.setUrl(modelData.index, browserUrl.text) }
                  }
                }
                RowLayout {
                  visible: !root.selectedName && root.lastView === "saved"
                  ActionButton { label: "Apply Last"; prominent: true; enabled: !root.busy && root.saved.length > 0; onClicked: root.applyLast() }
                  Text {
                    text: "Moves matches, reopens apps; never closes windows. Missing Chrome windows open blank."
                    color: root.muted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    font.pixelSize: 11
                  }
                }
                Text {
                  visible: root.displayedWorkspaces.length > 0
                  text: root.selectedName ? "WORKSPACE MAP  /  saved window geometry"
                    : root.lastView === "current" ? "LIVE WORKSPACE MAP" : "LAST SNAPSHOT  /  live matches shown"
                  color: Color.accent
                  font.bold: true
                }
                Repeater {
                  model: root.displayedWorkspaces
                  delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                      text: "Workspace " + modelData.name + "  -  " + modelData.indexes.length + " windows"
                      color: Color.foreground
                      font.bold: true
                    }
                    Rectangle {
                      id: grid
                      Layout.fillWidth: true
                      Layout.preferredHeight: 250
                      color: root.surface
                      radius: 8
                      clip: true
                      readonly property var bounds: modelData.bounds
                      readonly property real sx: width / bounds[2]
                      readonly property real sy: height / bounds[3]
                      Repeater {
                        model: modelData.indexes.filter(function(index) { return !!root.displayedEntries[index] })
                        delegate: Rectangle {
                          required property var modelData
                          readonly property var win: root.displayedEntries[modelData]
                          x: (win.at[0] - grid.bounds[0]) * grid.sx
                          y: (win.at[1] - grid.bounds[1]) * grid.sy
                          width: Math.max(28, win.size[0] * grid.sx)
                          height: Math.max(24, win.size[1] * grid.sy)
                          radius: 4
                          color: Color.background
                          border.color: Color.accent
                          clip: true
                          LiveWindowPreview {
                            anchors.fill: parent
                            address: win.address || ""
                            fallback: win.preview || ""
                            active: window.visible
                          }
                          Rectangle {
                            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                            height: 22
                            color: Qt.rgba(0, 0, 0, 0.8)
                            Text {
                              anchors.fill: parent
                              anchors.leftMargin: 4
                              text: win.class + "  /  " + win.title
                              color: "white"
                              elide: Text.ElideRight
                              verticalAlignment: Text.AlignVCenter
                              font.pixelSize: 10
                            }
                          }
                          ToolTip.visible: hover.hovered
                          ToolTip.text: win.title + " (workspace " + win.workspace + ")"
                          HoverHandler { id: hover }
                        }
                      }
                    }
                  }
                }
                Text {
                  visible: root.displayedWorkspaces.length > 0
                  text: root.selectedName ? "Approximate layout; Hyprland may re-tile when the setup starts."
                    : "Live windows update continuously. Closed windows have no preview."
                  color: root.muted
                  font.pixelSize: 11
                }
                RowLayout {
                  visible: !root.selectedName && root.lastView === "current"
                  ActionButton { label: "Save last now"; prominent: true; enabled: !root.busy; onClicked: root.saveLast() }
                  Text { text: root.savedAt ? "Saved " + new Date(root.savedAt * 1000).toLocaleString() : "Not saved yet"; color: root.muted }
                }
                Text {
                  text: root.selectedName ? "WINDOWS  -  Chrome needs a URL to create a missing window" : "WINDOWS"
                  color: Color.accent
                  font.bold: true
                  Layout.topMargin: 8
                }
                RowLayout {
                  visible: !root.selectedName
                  ActionButton { label: "Saved last (" + root.saved.length + ")"; active: root.lastView === "saved"; onClicked: root.lastView = "saved" }
                  ActionButton { label: "Open now (" + root.current.length + ")"; active: root.lastView === "current"; onClicked: root.lastView = "current" }
                }
                Repeater {
                  model: root.displayedEntries
                  delegate: Rectangle {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: detail.implicitHeight + 18
                    radius: 7
                    color: root.surface
                    ColumnLayout {
                      id: detail
                      anchors.left: parent.left; anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.margins: 9
                      Text {
                        text: "WORKSPACE " + modelData.workspace + "  /  " + modelData.class
                        color: Color.accent
                        font.pixelSize: 11
                        font.bold: true
                      }
                      Text {
                        text: modelData.title || "Untitled window"
                        color: Color.foreground
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                      }
                      LiveWindowPreview {
                        visible: !!modelData.address || !!modelData.preview
                        address: modelData.address || ""
                        fallback: modelData.preview || ""
                        active: window.visible && visible
                        Layout.fillWidth: true
                        Layout.preferredHeight: visible ? 100 : 0
                      }
                    }
                  }
                }
                Text {
                  visible: !root.selectedName
                  text: "SAVED LAST: " + root.saved.length + " windows. Last updates automatically as the desktop changes."
                  color: root.muted
                  wrapMode: Text.WordWrap
                  Layout.fillWidth: true
                }
              }
            }
          }
        }
      }
    }
  }

  component ActionButton: Button {
    property string label: ""
    property bool prominent: false
    property bool active: false
    implicitHeight: 38
    contentItem: Text {
      text: parent.label
      color: parent.prominent ? Color.background : Color.foreground
      verticalAlignment: Text.AlignVCenter
      horizontalAlignment: Text.AlignHCenter
      elide: Text.ElideRight
      font.bold: parent.prominent || parent.active
      leftPadding: 8
      rightPadding: 8
    }
    background: Rectangle {
      radius: 7
      color: parent.prominent ? Color.accent : parent.active ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.22)
            : parent.hovered ? Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.16) : root.surface
      opacity: parent.enabled ? 1 : 0.45
    }
  }
}
