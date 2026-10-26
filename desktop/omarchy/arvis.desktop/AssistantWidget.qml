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
    action.command = [commandPath].concat(args)
    action.running = true
  }
  function submit() {
    var text = prompt.text.trim()
    if (!text) return
    send(["ask", text])
    prompt.text = ""
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
  function requestText(item) {
    if (!item) return ""
    var p = item.details || {}
    if (p.questions) return p.questions.map(q => q.question + (q.options ? "\n" + q.options.map(o => "• " + o.label + ": " + o.description).join("\n") : "")).join("\n\n")
    return (p.reason || "Arvis needs permission for this action.") + "\n\n" + (p.command || JSON.stringify(p, null, 2))
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
      return JSON.stringify({revision: "0.1.1", monitor: panel.screen ? panel.screen.name : "", requestedMonitor: root.status.panel_monitor || "", opened: root.opened, visible: panel.visible})
    }
  }
  Timer { interval: 5000; running: true; repeat: true; onTriggered: { root.clock = Date.now()/1000; stateFile.reload() } }
  Process {
    id: action
    stderr: StdioCollector { onStreamFinished: if (text.trim()) root.commandError = text.trim() }
    onExited: { stateFile.reload(); Qt.callLater(root.pump) }
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
    visible: root.opened && screen && screen.name === (root.status.panel_monitor || (Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""))
    screen: root.QsWindow.window ? root.QsWindow.window.screen : null
    anchors { top: true; right: true }
    margins { top: 48; right: 18 }
    implicitWidth: Math.min(570, screen ? screen.width - 36 : 570)
    implicitHeight: Math.min(680, screen ? screen.height - 90 : 680)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "arvis-assistant"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.status.panel_focus ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand
    onVisibleChanged: if (visible) Qt.callLater(() => prompt.forceActiveFocus())

    Rectangle {
      anchors.fill: parent
      color: Commons.Color.popups.background
      radius: Style.cornerRadius
      border.color: Commons.Color.popups.border
      border.width: 1
      Column {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 14
        Row {
          width: parent.width
          spacing: 12
          Column {
            width: parent.width - 65
            spacing: 4
            Text { text: "ARVIS"; color: Commons.Color.foreground; font.pixelSize: 19; font.weight: Font.DemiBold; font.letterSpacing: 3 }
            Text { text: root.busy ? root.status.activity || "Working" : root.voiceLabel(); color: root.accent; font.pixelSize: 12; width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText }
          }
          Button { text: "×"; onClicked: root.close() }
        }
        Rectangle { width: parent.width; height: 1; color: Commons.Color.popups.border }
        Flickable {
          id: conversation
          width: parent.width
          height: Math.max(140, panel.height - 260 - (root.status.pending && root.status.pending.length ? 180 : 0))
          contentWidth: width
          contentHeight: messages.height
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          onContentHeightChanged: contentY = Math.max(0, contentHeight - height)
          Column {
            id: messages
            width: parent.width
            spacing: 18
            Text {
              visible: !root.status.history || root.status.history.length === 0
              text: "What are we working on?\n\nAsk me to investigate something, work in my own browser, or keep an eye on a task."
              width: parent.width
              color: Commons.Color.foreground
              opacity: 0.7
              font.pixelSize: 14
              wrapMode: Text.Wrap
            }
            Repeater {
              model: (root.status.history || []).slice(-30)
              delegate: Column {
                required property var modelData
                width: messages.width
                spacing: 5
                Text { text: modelData.role === "user" ? "YOU" : modelData.role === "error" ? "NEEDS ATTENTION" : "ARVIS"; color: modelData.role === "user" ? Commons.Color.foreground : root.accent; opacity: 0.75; font.pixelSize: 10; font.letterSpacing: 1.3 }
                Text { text: modelData.text; width: parent.width; color: Commons.Color.foreground; font.pixelSize: 14; wrapMode: Text.Wrap; textFormat: Text.PlainText }
              }
            }
            Text { visible: !!root.status.stream; text: root.status.stream || ""; width: parent.width; color: Commons.Color.foreground; font.pixelSize: 14; wrapMode: Text.Wrap; textFormat: Text.PlainText }
            Column {
              visible: !!root.status.preview
              width: parent.width
              spacing: 6
              Text { text: "LATEST WORKER VIEW"; color: root.accent; font.pixelSize: 10; font.letterSpacing: 1.3 }
              Image {
                width: parent.width
                height: width * 0.625
                source: root.status.preview ? "file://" + root.status.preview + "?v=" + root.status.preview_at : ""
                cache: false
                asynchronous: true
                fillMode: Image.PreserveAspectFit
              }
            }
          }
        }
        Column {
          id: approvalColumn
          width: parent.width
          spacing: 8
          visible: root.status.pending && root.status.pending.length > 0
          property var item: visible ? root.status.pending[0] : ({details: {}})
          Text { text: "YOUR INPUT"; color: root.accent; font.pixelSize: 11; font.letterSpacing: 1 }
          Flickable {
            width: parent.width; height: 80; contentHeight: approvalText.height; clip: true
            Text { id: approvalText; width: parent.width; text: root.requestText(approvalColumn.item); color: Commons.Color.foreground; font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText }
          }
          Row {
            spacing: 8
            Button { text: "Allow once"; visible: !parent.parent.item.details.questions; onClicked: root.send(["respond", parent.parent.item.id, "allow"]) }
            Button { text: "Decline"; onClicked: root.send(["respond", parent.parent.item.id, "deny"]) }
            TextField { id: response; width: 230; visible: !!parent.parent.item.details.questions; placeholderText: "Your answer…"; onAccepted: { root.send(["respond", parent.parent.item.id, "answer", text]); text = "" } }
          }
        }
        TextField {
          id: prompt
          width: parent.width
          placeholderText: root.busy ? "Add a task to the queue…" : "Ask Arvis…"
          onAccepted: root.submit()
          Keys.onEscapePressed: root.close()
        }
        Row {
          spacing: 8
          Button { text: "Speak"; enabled: !root.busy; onClicked: root.send(["activate"]) }
          Button { text: root.status.muted ? "Unmute" : "Mute"; onClicked: root.send(["mute"]) }
          Button { text: "Stop"; onClicked: root.send(["cancel"]) }
          Button { text: "New chat"; enabled: !root.busy; onClicked: root.send(["new-conversation"]) }
        }
        Text { visible: !!root.commandError; text: root.commandError; width: parent.width; color: "#fca5a5"; wrapMode: Text.Wrap; font.pixelSize: 11; textFormat: Text.PlainText }
      }
    }
  }
}
