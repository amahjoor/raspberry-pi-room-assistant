import QtQuick
import QtQuick.Controls as C
import QtQuick.Layouts
import "ChatFormat.js" as ChatFormat

Item {
  id: root
  property var status: ({history: [], pending: []})
  property bool online: false
  property string commandError: ""
  property color backgroundColor: "#16181f"
  property color foregroundColor: "#e8e9ef"
  property color accentColor: "#afc2ff"
  readonly property color secondary: root.fade(foregroundColor, 0.58)
  readonly property color lineColor: root.fade(foregroundColor, 0.09)
  readonly property color raised: Qt.tint(backgroundColor, root.fade(foregroundColor, 0.045))
  readonly property bool busy: online && (status.state === "working" || status.state === "needs_input")
  readonly property bool audioActive: status.voice === "listening" || status.voice === "transcribing" || status.voice === "speaking"
  // A wake-model failure can still support manual capture; keep that fallback.
  readonly property bool voiceAvailable: online && status.voice !== "disabled" && status.voice !== "starting"
  readonly property string microphoneLabel: status.muted ? "Microphone paused"
    : status.voice === "disabled" ? "Voice off" : status.voice === "unavailable" ? "Voice unavailable"
    : status.voice === "starting" ? "Starting voice…" : status.voice === "idle" ? (status.wake_ready === false ? "Voice ready" : "Listening for Arvis")
    : audioActive ? activity : "Microphone ready"
  readonly property bool canStop: online && (busy || audioActive || (status.queued || 0) > 0)
  readonly property var approval: status.pending && status.pending.length ? status.pending[0] : null
  readonly property bool answering: !!approval && !!approval.details.questions
  readonly property string activity: !online ? "Offline" : status.state === "needs_input" ? "Needs your input"
    : status.voice === "listening" ? "Listening…" : status.voice === "transcribing" ? "Transcribing…"
    : status.voice === "speaking" ? "Speaking…" : busy ? (status.activity || "Thinking…") : "Ready"
  readonly property bool showStream: !!status.stream && !(status.history && status.history.length
    && status.history[status.history.length - 1].role === "assistant" && status.history[status.history.length - 1].text === status.stream)
  property alias draft: prompt.text
  property bool submitting: false
  property string submittedDraft: ""
  property string lastHistory: ""
  property string lastApproval: ""
  property string answerDraft: ""
  property string localError: ""
  property bool previewExpanded: false
  signal command(var args)
  signal closeRequested()

  function fade(color, opacity) { return Qt.rgba(color.r, color.g, color.b, opacity) }
  function focusComposer() { prompt.forceActiveFocus() }
  function submitDraft() {
    var text = prompt.text.trim()
    if (!online || submitting || !text) return
    if (text.length > 30000) { localError = "Please shorten this message to 30,000 characters."; return }
    localError = ""
    submittedDraft = prompt.text
    submitting = true
    conversation.followTail = true
    command(["ask", text])
  }
  function commandFinished(args, success) {
    if (args[0] === "ask") {
      submitting = false
      if (success && prompt.text === submittedDraft) prompt.text = ""
    }
    if (success && args[0] === "respond" && args[1] === lastApproval) answerDraft = ""
  }
  function syncHistory() {
    var history = status.history || []
    var signature = JSON.stringify(history)
    if (signature !== lastHistory) {
      lastHistory = signature
      // Keep existing delegates alive during streaming and status heartbeats.
      var prefix = 0
      while (prefix < historyModel.count && prefix < history.length) {
        var old = historyModel.get(prefix), next = history[prefix]
        if (old.sender !== next.role || old.body !== next.text || old.timestamp !== (next.time || 0)) break
        prefix++
      }
      if (prefix < historyModel.count) historyModel.remove(prefix, historyModel.count - prefix)
      for (var i = prefix; i < history.length; i++)
        historyModel.append({sender: history[i].role, body: history[i].text, timestamp: history[i].time || 0})
      if (!history.length) conversation.followTail = true
      conversation.followLatest()
    }
    // Read the new status directly: derived bindings may still describe the
    // previous request while onStatusChanged is running.
    var pending = status.pending || []
    var id = pending.length ? pending[0].id : ""
    if (id !== lastApproval) { lastApproval = id; answerDraft = "" }
  }
  function rich(text) { return ChatFormat.richText(text, String(foregroundColor), String(raised), String(accentColor)) }
  function copyText(text) { clipboard.text = text; clipboard.selectAll(); clipboard.copy(); clipboard.deselect() }
  function requestText() {
    if (!approval) return ""
    var p = approval.details || {}
    if (p.questions) return p.questions.map(q => q.question + (q.options ? "\n" + q.options.map(o => "• " + o.label + ": " + o.description).join("\n") : "")).join("\n\n")
    return (p.reason || "Review this action before Arvis continues.") + "\n\n" + (typeof p.command === "string" ? p.command : JSON.stringify(p.command || p, null, 2))
  }
  onStatusChanged: syncHistory()
  Component.onCompleted: syncHistory()
  Keys.onEscapePressed: closeRequested()
  ListModel { id: historyModel }
  TextEdit { id: clipboard; visible: false; textFormat: TextEdit.PlainText }

  component Action: ChatAction { ink: root.foregroundColor; tint: root.accentColor }
  component Body: TextEdit {
    readOnly: true
    selectByMouse: true
    wrapMode: TextEdit.Wrap
    color: root.foregroundColor
    selectionColor: root.fade(root.accentColor, 0.35)
    selectedTextColor: root.foregroundColor
    font.family: "sans-serif"
    font.pixelSize: 14
    textFormat: TextEdit.RichText
    onLinkActivated: function(link) { if (/^https?:\/\//i.test(link)) Qt.openUrlExternally(link) }
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: 0
    RowLayout {
      Layout.fillWidth: true
      Layout.margins: 20
      Layout.bottomMargin: 16
      spacing: 11
      Rectangle {
        width: 32; height: 32; radius: 10
        color: root.fade(root.accentColor, 0.12)
        Text { anchors.centerIn: parent; text: "A"; color: root.accentColor; font.family: "sans-serif"; font.pixelSize: 17; font.weight: Font.DemiBold }
      }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: 2
        Text { text: "Arvis"; color: root.foregroundColor; font.family: "sans-serif"; font.pixelSize: 16; font.weight: Font.DemiBold }
        RowLayout {
          spacing: 5
          Rectangle { width: 5; height: 5; radius: 3; color: root.online ? root.accentColor : root.secondary }
          Text { text: root.online ? "Personal assistant" : "Assistant is offline"; color: root.secondary; font.family: "sans-serif"; font.pixelSize: 11 }
        }
      }
      Item { Layout.fillWidth: true }
      Action {
        objectName: "newChatButton"
        glyphName: "new"
        label: "New chat"
        compact: true
        hint: "New chat"
        enabled: root.online && !root.canStop && !root.submitting
        onClicked: root.command(["new-conversation"])
      }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: root.lineColor }

    Item {
      Layout.fillWidth: true
      Layout.fillHeight: true
      Layout.minimumHeight: 100
      ListView {
        id: conversation
        objectName: "conversationList"
        anchors.fill: parent
        anchors.leftMargin: 22
        anchors.rightMargin: 18
        clip: true
        spacing: 22
        topMargin: 22
        bottomMargin: 16
        boundsBehavior: Flickable.StopAtBounds
        model: historyModel
        property bool followTail: true
        function followLatest() { if (followTail) Qt.callLater(() => { if (followTail) positionViewAtEnd() }) }
        onContentHeightChanged: followLatest()
        onHeightChanged: followLatest()
        onContentYChanged: if (moving || dragging || flicking) followTail = contentY + height >= contentHeight + originY - 45
        C.ScrollBar.vertical: C.ScrollBar {
          objectName: "conversationScrollbar"
          policy: C.ScrollBar.AsNeeded
          width: 5
          onPressedChanged: {
            if (pressed) conversation.followTail = false
            else conversation.followTail = conversation.contentY + conversation.height >= conversation.contentHeight + conversation.originY - 45
          }
        }
        delegate: Item {
          id: message
          required property string sender
          required property string body
          required property double timestamp
          readonly property bool userMessage: sender === "user"
          width: conversation.width - 8
          height: messageColumn.implicitHeight
          Column {
            id: messageColumn
            width: parent.width
            spacing: 7
            Text {
              visible: !message.userMessage
              text: message.sender === "error" ? "Needs attention" : "Arvis"
              color: message.sender === "error" ? "#e6a18e" : root.secondary
              font.family: "sans-serif"
              font.pixelSize: 11
              font.weight: Font.Medium
            }
            Rectangle {
              id: bubble
              x: message.userMessage ? parent.width - width : 0
              width: message.userMessage ? parent.width * 0.87 : parent.width
              height: messageBody.height + (message.userMessage || message.sender === "error" ? 24 : 0)
              radius: 13
              color: message.userMessage ? root.fade(root.foregroundColor, 0.065) : message.sender === "error" ? Qt.rgba(0.8,0.25,0.15,0.08) : "transparent"
              Body {
                id: messageBody
                x: message.userMessage || message.sender === "error" ? 14 : 0
                y: message.userMessage || message.sender === "error" ? 12 : 0
                width: parent.width - x * 2
                height: contentHeight
                textFormat: message.userMessage || message.sender === "error" ? TextEdit.PlainText : TextEdit.RichText
                text: message.userMessage || message.sender === "error" ? message.body : root.rich(message.body)
              }
            }
            Action {
              visible: !message.userMessage && message.sender !== "error"
              glyphName: "copy"; compact: true; hint: "Copy reply"
              opacity: hovered || activeFocus ? 1 : 0.45
              onClicked: root.copyText(message.body)
            }
          }
        }
        footer: Column {
          width: conversation.width - 8
          spacing: 14
          Column {
            visible: root.showStream
            width: parent.width
            spacing: 8
            Text { text: "Arvis"; color: root.secondary; font.family: "sans-serif"; font.pixelSize: 11; font.weight: Font.Medium }
            Body { width: parent.width; height: contentHeight; text: root.rich(root.status.stream || "") }
          }
          Row {
            visible: root.canStop
            spacing: 8
            Rectangle {
              width: 6; height: 6; radius: 3
              anchors.verticalCenter: parent.verticalCenter
              color: root.accentColor
              SequentialAnimation on opacity {
                running: root.canStop && root.visible; loops: Animation.Infinite
                NumberAnimation { to: 0.3; duration: 650 }
                NumberAnimation { to: 1; duration: 650 }
              }
            }
            Text { width: conversation.width - 30; text: root.activity; color: root.secondary; font.family: "sans-serif"; font.pixelSize: 12; elide: Text.ElideRight; textFormat: Text.PlainText }
          }
          Rectangle {
            visible: !!root.approval
            width: parent.width
            height: approvalContent.implicitHeight + 28
            radius: 12
            color: root.raised
            border.color: root.fade(root.accentColor, 0.22)
            ColumnLayout {
              id: approvalContent
              x: 14; y: 14; width: parent.width - 28
              spacing: 10
              Text { text: root.answering ? "A quick question" : "Permission needed"; color: root.accentColor; font.family: "sans-serif"; font.pixelSize: 12; font.weight: Font.DemiBold }
              C.ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(160, requestBody.contentHeight)
                clip: true
                contentWidth: availableWidth
                Body { id: requestBody; width: parent.availableWidth || approvalContent.width; text: root.requestText(); textFormat: TextEdit.PlainText; font.pixelSize: 12; height: contentHeight }
              }
              C.TextField {
                id: answer
                objectName: "approvalInput"
                visible: root.answering
                Layout.fillWidth: true
                text: root.answerDraft
                onTextEdited: root.answerDraft = text
                placeholderText: "Your answer…"
                color: root.foregroundColor
                placeholderTextColor: root.secondary
                font.family: "sans-serif"
                padding: 10
                background: Rectangle { radius: 7; color: root.backgroundColor; border.color: answer.activeFocus ? root.accentColor : root.lineColor }
                onAccepted: if (text.trim() && root.approval) root.command(["respond", root.approval.id, "answer", text.trim()])
              }
              RowLayout {
                Action { label: "Decline"; onClicked: if (root.approval) root.command(["respond", root.approval.id, "deny"]) }
                Item { Layout.fillWidth: true }
                Action {
                  objectName: "approvalConfirm"
                  label: root.answering ? "Send answer" : "Allow once"
                  prominent: true
                  enabled: !root.answering || !!answer.text.trim()
                  onClicked: if (root.approval) root.command(["respond", root.approval.id, root.answering ? "answer" : "allow", root.answering ? answer.text.trim() : ""])
                }
              }
            }
          }
          Column {
            visible: !!root.status.preview
            width: parent.width
            spacing: 8
            Action { label: root.previewExpanded ? "Hide worker view" : "Show worker view"; glyphName: "down"; compact: true; onClicked: root.previewExpanded = !root.previewExpanded }
            Image {
              visible: root.previewExpanded
              width: parent.width; height: visible ? width * 0.625 : 0
              source: root.previewExpanded && root.status.preview ? "file://" + root.status.preview + "?v=" + root.status.preview_at : ""
              cache: false; asynchronous: true; fillMode: Image.PreserveAspectFit
            }
          }
        }
      }
      ColumnLayout {
        visible: historyModel.count === 0 && !root.canStop && !root.approval
        anchors.centerIn: parent
        width: parent.width - 64
        spacing: 13
        Text { text: "What are we working on?"; color: root.foregroundColor; font.family: "sans-serif"; font.pixelSize: 22; font.weight: Font.Medium; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Text { text: "Think it through. Hand off a task.\nKeep moving."; color: root.secondary; font.family: "sans-serif"; font.pixelSize: 13; lineHeight: 1.35; Layout.bottomMargin: 10 }
        Repeater {
          model: ["Explain what’s on my screen", "Help me work through a task", "Remember a preference"]
          delegate: C.AbstractButton {
            required property string modelData
            Layout.fillWidth: true
            implicitHeight: 40
            hoverEnabled: true
            Accessible.name: modelData
            background: Rectangle { radius: 9; color: parent.hovered ? root.fade(root.foregroundColor, 0.07) : "transparent"; border.color: root.lineColor }
            contentItem: Text { text: parent.modelData + "  ↗"; leftPadding: 13; verticalAlignment: Text.AlignVCenter; color: root.secondary; font.family: "sans-serif"; font.pixelSize: 12 }
            onClicked: {
              prompt.text = modelData === "Explain what’s on my screen" ? "Look at my current window and explain what you see."
                : modelData === "Remember a preference" ? "Remember that I prefer " : "Help me work through "
              root.focusComposer()
              prompt.cursorPosition = prompt.length
            }
          }
        }
      }
      Action {
        objectName: "latestButton"
        visible: !conversation.followTail && historyModel.count > 0
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 10
        label: "Latest"; glyphName: "down"; prominent: true; compact: true
        onClicked: { conversation.followTail = true; conversation.followLatest() }
      }
    }

    ColumnLayout {
      Layout.fillWidth: true
      Layout.margins: 16
      Layout.topMargin: 6
      spacing: 8
      RowLayout {
        visible: (root.status.queued || 0) > 0
        Layout.fillWidth: true
        Text { text: root.status.queued + ((root.status.queued || 0) === 1 ? " message queued" : " messages queued"); color: root.secondary; font.family: "sans-serif"; font.pixelSize: 11 }
        Item { Layout.fillWidth: true }
      }
      Text {
        visible: !!(root.commandError || root.localError)
        Layout.fillWidth: true
        text: root.localError || root.commandError
        color: "#e6a18e"; font.family: "sans-serif"; font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText
      }
      Rectangle {
        Layout.fillWidth: true
        implicitHeight: composerContent.implicitHeight + 22
        radius: 14
        color: root.raised
        border.color: prompt.activeFocus ? root.fade(root.accentColor, 0.4) : root.lineColor
        Behavior on border.color { ColorAnimation { duration: 120 } }
        ColumnLayout {
          id: composerContent
          x: 11; y: 11; width: parent.width - 22
          spacing: 8
          C.ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(44, Math.min(130, prompt.contentHeight + 4))
            clip: true
            contentWidth: availableWidth
            C.TextArea {
              id: prompt
              objectName: "composerInput"
              width: parent.availableWidth || composerContent.width
              wrapMode: TextEdit.Wrap
              selectByMouse: true
              color: root.foregroundColor
              placeholderTextColor: root.secondary
              placeholderText: !root.online ? "Arvis is offline" : root.busy ? "Add a follow-up…" : "Ask Arvis anything…"
              font.family: "sans-serif"
              font.pixelSize: 14
              leftPadding: 3; rightPadding: 3; topPadding: 3; bottomPadding: 3
              background: null
              selectionColor: root.fade(root.accentColor, 0.35)
              selectedTextColor: root.foregroundColor
              Keys.onPressed: function(event) {
                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & Qt.ShiftModifier) && !inputMethodComposing) {
                  root.submitDraft(); event.accepted = true
                } else if (event.key === Qt.Key_Escape) { root.closeRequested(); event.accepted = true }
              }
            }
          }
          RowLayout {
            Layout.fillWidth: true
            spacing: 4
            Action {
              objectName: "voiceButton"
              glyphName: "mic"; hint: root.status.muted ? "Unmute the microphone to speak" : !root.voiceAvailable ? (root.status.voice_message || root.microphoneLabel) : root.status.voice === "unavailable" ? "Try manual voice capture" : "Speak to Arvis"
              selected: root.status.voice === "listening"
              enabled: root.voiceAvailable && !root.busy && !root.audioActive && !root.status.muted
              onClicked: root.command(["activate"])
            }
            Action {
              objectName: "muteButton"
              label: root.microphoneLabel
              hint: !root.voiceAvailable && !root.status.muted ? (root.status.voice_message || root.microphoneLabel) : root.status.muted ? "Resume microphone" : "Pause microphone"
              compact: true
              ink: root.secondary
              enabled: root.online && (root.voiceAvailable || root.status.muted)
              onClicked: root.command(["mute"])
            }
            Item { Layout.fillWidth: true }
            Action {
              objectName: "stopButton"
              visible: root.canStop
              glyphName: "stop"; hint: "Stop current and queued work"
              prominent: !prompt.text.trim()
              onClicked: root.command(["cancel"])
            }
            Action {
              objectName: "sendButton"
              glyphName: "send"
              prominent: true
              hint: root.submitting ? "Sending…" : root.busy ? "Queue message" : "Send message"
              visible: !root.canStop || !!prompt.text.trim()
              enabled: root.online && !root.submitting && !!prompt.text.trim()
              onClicked: root.submitDraft()
            }
          }
        }
      }
      Text {
        Layout.alignment: Qt.AlignHCenter
        text: root.submitting ? "Sending…" : "Enter to send · Shift + Enter for a new line"
        color: root.fade(root.foregroundColor, 0.38)
        font.family: "sans-serif"
        font.pixelSize: 10
      }
    }
  }
}
