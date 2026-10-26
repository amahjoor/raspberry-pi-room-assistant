import QtQuick
import QtQuick.Controls as C

C.AbstractButton {
  id: control
  function fade(color, opacity) { return Qt.rgba(color.r, color.g, color.b, opacity) }
  property string glyphName: ""
  property string label: ""
  property string hint: label
  property color ink: "#e8e9ef"
  property color tint: "#a7baff"
  property bool prominent: false
  property bool selected: false
  property bool compact: false
  implicitWidth: label ? content.implicitWidth + 20 : (compact ? 30 : 36)
  implicitHeight: compact ? 30 : 36
  hoverEnabled: true
  focusPolicy: Qt.TabFocus
  Accessible.name: hint
  opacity: enabled ? 1 : 0.35
  C.ToolTip.visible: hovered && hint.length > 0
  C.ToolTip.text: hint
  C.ToolTip.delay: 650

  background: Rectangle {
    radius: 9
    color: control.prominent ? control.tint
      : control.down ? control.fade(control.ink, 0.13)
      : control.hovered || control.selected ? control.fade(control.ink, 0.07) : "transparent"
    border.width: control.activeFocus ? 1 : 0
    border.color: control.tint
    Behavior on color { ColorAnimation { duration: 100 } }
  }
  contentItem: Row {
    id: content
    spacing: 7
    readonly property color foreground: control.prominent ? "#11141b" : control.ink
    Canvas {
      id: drawing
      width: control.glyphName ? 18 : 0
      height: 18
      anchors.verticalCenter: parent.verticalCenter
      visible: !!control.glyphName
      onPaint: {
        var c = getContext("2d")
        c.reset()
        c.strokeStyle = content.foreground
        c.fillStyle = content.foreground
        c.lineWidth = 1.5
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(x1, y1, x2, y2) { c.moveTo(x1,y1); c.lineTo(x2,y2) }
        c.beginPath()
        if (control.glyphName === "send") {
          line(9,14,9,4); line(4.5,8.5,9,4); line(9,4,13.5,8.5)
        } else if (control.glyphName === "stop") {
          c.roundedRect(4.5,4.5,9,9,1.5,1.5); c.fill()
        } else if (control.glyphName === "mic") {
          c.roundedRect(6.3,2,5.4,9,2.7,2.7)
          c.moveTo(3.8,8); c.bezierCurveTo(3.8,15,14.2,15,14.2,8)
          line(9,13,9,16); line(6,16,12,16)
        } else if (control.glyphName === "new") {
          c.moveTo(3,3); c.lineTo(15,3); c.lineTo(15,12)
          c.lineTo(8,12); c.lineTo(3,16); c.closePath()
          line(6,7.5,12,7.5); line(9,5,9,10)
        } else if (control.glyphName === "copy") {
          c.rect(6,6,9,10); line(3,12,3,2); line(3,2,12,2)
        } else if (control.glyphName === "down") {
          line(5,7,9,11); line(9,11,13,7)
        } else if (control.glyphName === "arrow") {
          line(4,9,14,9); line(10,5,14,9); line(14,9,10,13)
        }
        c.stroke()
      }
      Connections { target: control; function onGlyphNameChanged() { drawing.requestPaint() } }
      Connections { target: content; function onForegroundChanged() { drawing.requestPaint() } }
    }
    Text {
      visible: !!control.label
      text: control.label
      color: content.foreground
      font.family: "sans-serif"
      font.pixelSize: 12
      font.weight: Font.Medium
      anchors.verticalCenter: parent.verticalCenter
    }
  }
  leftPadding: label ? 10 : (width - 18) / 2
  rightPadding: leftPadding
  topPadding: (height - 18) / 2
  bottomPadding: topPadding
}
