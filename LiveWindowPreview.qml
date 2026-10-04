import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Wayland

Item {
  id: root

  property string address: ""
  property string fallback: ""
  property bool active: true

  function normalizeAddress(value) {
    var text = String(value || "").toLowerCase()
    return text.startsWith("0x") ? text : "0x" + text
  }

  readonly property var toplevel: {
    if (!active || !address) return null
    var wanted = normalizeAddress(address)
    var workspaces = Hyprland.workspaces.values || []
    for (var i = 0; i < workspaces.length; i++) {
      var windows = workspaces[i].toplevels ? workspaces[i].toplevels.values || [] : []
      for (var j = 0; j < windows.length; j++) {
        var win = windows[j]
        var ipc = win.lastIpcObject
        if (ipc && normalizeAddress(ipc.address) === wanted && win.wayland)
          return win
      }
    }
    return null
  }

  clip: true

  Image {
    anchors.fill: parent
    visible: root.toplevel === null && !!root.fallback
    source: visible ? "file://" + root.fallback : ""
    fillMode: Image.PreserveAspectCrop
    cache: false
  }

  Loader {
    anchors.fill: parent
    active: root.active && root.toplevel !== null
    sourceComponent: ScreencopyView {
      captureSource: root.toplevel.wayland
      live: true
      paintCursor: false
    }
  }
}
