import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Ui
import qs.Commons

Item {
  id: root
  property var shell: null
  property var manifest: null
  property bool closingFromHost: false
  property var current: []
  property var saved: []
  property var savedAt: null
  property string error: ""
  property string notice: ""
  readonly property string script: Qt.resolvedUrl("bin/omarchy-last-session").toString().replace(/^file:\/\//, "")
  readonly property string pluginId: (manifest && manifest.id) || "io.github.hjanuschka.restore"

  function open() {
    window.visible = true
    refresh()
  }

  function close() {
    closingFromHost = true
    window.visible = false
    closingFromHost = false
  }

  function dismiss() {
    if (shell && typeof shell.hide === "function") shell.hide(pluginId)
    else close()
  }

  function refresh() {
    if (!preview.running) {
      error = ""
      preview.running = true
    }
  }

  function save() {
    if (!saveProcess.running && !preview.running) {
      error = ""
      notice = ""
      saveProcess.running = true
    }
  }

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
          root.savedAt = data.saved_at
        } catch (e) {
          root.error = "Could not read session preview: " + e
        }
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim()) root.error = text.trim()
    }
  }

  Process {
    id: saveProcess
    command: [root.script, "save"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.notice = text.trim()
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim()) root.error = text.trim()
    }
    onExited: root.refresh()
  }

  PanelWindow {
    id: window
    visible: false
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-restore"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: Qt.rgba(0, 0, 0, 0.65)
      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }
    }

    Rectangle {
      anchors.centerIn: parent
      width: Math.min(parent.width - 32, 680)
      height: Math.min(parent.height - 32, 620)
      radius: 12
      color: Color.background
      MouseArea { anchors.fill: parent; onClicked: {} }

      FocusScope {
        anchors.fill: parent
        focus: true
        Keys.onEscapePressed: root.dismiss()

        ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        Text {
          text: "Session preview"
          color: Color.foreground
          font.pixelSize: 22
          font.bold: true
        }
        Text {
          text: "Windows only. Chrome restores its own tabs; a saved window may not reopen if Chrome forgets it."
          color: Color.foreground
          wrapMode: Text.WordWrap
          Layout.fillWidth: true
        }
        RowLayout {
          Button {
            text: "Save now"
            enabled: !saveProcess.running && !preview.running
            onClicked: root.save()
          }
          Button {
            text: "Refresh"
            enabled: !preview.running
            onClicked: root.refresh()
          }
          Text {
            text: root.notice || root.error
            color: root.error ? Color.urgent : Color.foreground
            elide: Text.ElideRight
            Layout.fillWidth: true
          }
        }

        ScrollView {
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

          ColumnLayout {
            width: parent.width
            spacing: 12

            Repeater {
              model: [
                { label: "Would save now", windows: root.current },
                { label: "Saved snapshot" + (root.savedAt ? " - " + new Date(root.savedAt * 1000).toLocaleString() : " - none"), windows: root.saved }
              ]
              delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: 4
                Text {
                  text: modelData.label + " (" + modelData.windows.length + ")"
                  color: Color.accent
                  font.bold: true
                }
                Text {
                  visible: modelData.windows.length === 0
                  text: "No restorable windows"
                  color: Color.foreground
                }
                Repeater {
                  model: modelData.windows
                  delegate: Text {
                    required property var modelData
                    text: "Workspace " + modelData.workspace + "  |  " + modelData.class + "  |  " + modelData.title
                    color: Color.foreground
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    ToolTip.visible: hover.hovered
                    ToolTip.text: text
                    HoverHandler { id: hover }
                  }
                }
              }
            }
          }
        }
      }
    }
    }
  }
}
