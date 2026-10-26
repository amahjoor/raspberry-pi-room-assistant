import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Ui
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

Ui.BarWidget {
  id: root
  moduleName: "arvis.desktop"
  property var status: ({running: false, state: "offline", voice: "disabled", history: [], pending: []})
  property double clock: Date.now() / 1000
  readonly property bool online: status.running === true && clock - (status.updated_at || 0) < 20
  readonly property bool busy: online && (status.state === "working" || status.state === "needs_input")
  readonly property bool listening: status.voice === "listening"
  readonly property bool opened: online && status.panel_open === true
  readonly property string commandPath: Quickshell.env("HOME") + "/.local/bin/arvis-pc"
  readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/arvis/status.json"
  readonly property color accent: Commons.Color.accent
  property string commandError: ""
  property var commandQueue: []
  property var currentCommand: []
  implicitWidth: label.implicitWidth + Style.space(22)
  implicitHeight: barSize

  function close() { send(["hide"]) }
  function send(args) {
    commandQueue = commandQueue.concat([args])
    pump()
  }
  function pump() {
    if (action.running || commandQueue.length === 0) return
    var args = commandQueue[0]
    commandQueue = commandQueue.slice(1)
    commandError = ""
    currentCommand = args
    action.command = [commandPath].concat(args)
    action.running = true
  }
  function load(raw) {
    try { status = JSON.parse(raw) } catch (e) { commandError = "Cannot read Arvis status" }
  }
  function voiceLabel() {
    if (!online) return "Offline"
    if (status.muted) return "Microphone paused"
    if (status.voice === "idle") return "Listening for Arvis"
    if (status.voice === "listening") return "Listening…"
    if (status.voice === "transcribing") return "Understanding…"
    if (status.voice === "speaking") return "Speaking"
    return status.voice_message || status.voice || "Ready"
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    onLoaded: root.load(text())
    onFileChanged: reload()
  }
  IpcHandler {
    target: "arvis-view-" + (root.QsWindow.window && root.QsWindow.window.screen ? root.QsWindow.window.screen.name : "pending")
    function status(): string {
      return JSON.stringify({revision: "0.2.0", monitor: panel.screen ? panel.screen.name : "", requestedMonitor: root.status.panel_monitor || "", opened: root.opened, visible: panel.visible, focusPrimed: panel.focusPrimed, exclusiveFocus: panel.WlrLayershell.keyboardFocus === WlrKeyboardFocus.Exclusive})
    }
  }
  Timer { interval: 5000; running: true; repeat: true; onTriggered: { root.clock = Date.now()/1000; stateFile.reload() } }
  Process {
    id: action
    stderr: StdioCollector { onStreamFinished: if (text.trim()) root.commandError = text.trim() }
    onExited: function(exitCode, exitStatus) {
      if (exitCode !== 0 && !root.commandError) root.commandError = "Arvis could not complete that request. Your draft is still here."
      chat.commandFinished(root.currentCommand, exitCode === 0)
      stateFile.reload()
      Qt.callLater(root.pump)
    }
  }

  Row {
    id: label
    anchors.centerIn: parent
    spacing: Style.space(7)
    Rectangle {
      anchors.verticalCenter: parent.verticalCenter
      width: 7; height: 7; radius: 4
      color: root.listening ? "#6ee7b7" : root.busy ? root.accent : root.online ? root.bar.barForeground : "#777777"
      SequentialAnimation on opacity {
        running: root.busy || root.listening; loops: Animation.Infinite
        NumberAnimation { to: 0.35; duration: 650 }
        NumberAnimation { to: 1; duration: 650 }
      }
    }
    Text { text: "Arvis"; color: root.bar.barForeground; font.family: root.bar.fontFamily; font.pixelSize: Style.font.body; font.weight: Font.Medium }
  }
  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    hoverEnabled: true
    onClicked: function(mouse) {
      root.send([mouse.button === Qt.RightButton ? "mute" : mouse.button === Qt.MiddleButton ? "activate" : "toggle"])
    }
    onEntered: if (root.bar) root.bar.showTooltip(root, root.voiceLabel() + (root.busy ? " · " + (root.status.activity || "Working") : ""))
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }

  PanelWindow {
    id: panel
    property bool focusPrimed: false
    function primeFocus() {
      if (visible && backingWindowVisible && root.status.panel_focus) focusPrimeTimer.restart()
    }
    visible: root.opened && screen && screen.name === (root.status.panel_monitor || (Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""))
    screen: root.QsWindow.window ? root.QsWindow.window.screen : null
    anchors { top: true; right: true }
    margins { top: 48; right: 18 }
    implicitWidth: Math.min(610, screen ? screen.width - 36 : 610)
    implicitHeight: Math.min(790, screen ? screen.height - 90 : 790)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "arvis-assistant"
    WlrLayershell.layer: WlrLayer.Overlay
    // A persistent Exclusive grab also captures pointer input in Hyprland,
    // preventing a second click on the bar. Prime keyboard focus briefly,
    // then let pointer events reach the bar and other applications again.
    WlrLayershell.keyboardFocus: !visible ? WlrKeyboardFocus.None
      : root.status.panel_focus && !focusPrimed ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand
    onVisibleChanged: {
      focusPrimeTimer.stop()
      focusPrimed = false
      if (visible) {
        primeFocus()
        Qt.callLater(() => { if (panel.visible) chat.focusComposer() })
      }
    }
    onBackingWindowVisibleChanged: primeFocus()
    Timer {
      id: focusPrimeTimer
      interval: 75
      onTriggered: if (panel.visible) panel.focusPrimed = true
    }

    Rectangle {
      anchors.fill: parent
      color: Commons.Color.popups.background
      radius: 18
      border.color: Qt.rgba(Commons.Color.foreground.r, Commons.Color.foreground.g, Commons.Color.foreground.b, 0.13)
      border.width: 1
      ConversationView {
        id: chat
        anchors.fill: parent
        anchors.margins: 1
        status: root.status
        online: root.online
        commandError: root.commandError
        backgroundColor: Commons.Color.popups.background
        foregroundColor: Commons.Color.foreground
        accentColor: root.accent
        onCommand: function(args) { root.send(args) }
        onCloseRequested: root.close()
      }
    }
  }
}
