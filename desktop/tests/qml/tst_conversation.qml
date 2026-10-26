// Run with qmltestrunner; all input targets this isolated test view.
import QtQuick
import QtQuick.Controls as C
import QtTest
import "../../omarchy/arvis.desktop" as Arvis

Item {
    id: fixture
    width: 590
    height: 780
    property var commands: []
    property int closeRequests: 0
    Rectangle { anchors.fill: parent; color: '#16191d' }
    Arvis.ConversationView {
        id: chat
        anchors.fill: parent
        backgroundColor: '#16191d'
        foregroundColor: '#e8ebef'
        accentColor: '#b5c8f1'
        online: true
        onCommand: function(args) { fixture.commands = fixture.commands.concat([args]) }
        onCloseRequested: fixture.closeRequests++
    }
    TestCase {
        name: 'ArvisConversation'
        when: windowShown
        function state(extra) {
            chat.status = Object.assign({running: true, state: 'ready', voice: 'idle', muted: false, history: [], pending: [], stream: '', queued: 0, activity: 'Ready'}, extra || {})
            wait(20)
        }
        function target(name) {
            var item = findChild(chat, name)
            verify(item !== null, 'Missing UI control ' + name)
            return item
        }
        function click(name) {
            var item = target(name)
            verify(item.visible, name + ' should be visible')
            verify(item.enabled, name + ' should be enabled')
            mouseClick(item, item.width / 2, item.height / 2)
            wait(20)
        }
        function init() {
            failOnWarning(/.*/)
            fixture.commands = []
            fixture.closeRequests = 0
            chat.commandError = ''
            chat.submitting = false
            chat.online = true
            chat.draft = ''
            fixture.width = 590
            fixture.height = 780
            state()
        }
        function test_enter_submits_and_shift_enter_adds_newline() {
            chat.draft = 'Explain this error'
            chat.focusComposer()
            keyClick(Qt.Key_Return, Qt.ShiftModifier)
            compare(fixture.commands.length, 0)
            verify(chat.draft.indexOf('\n') !== -1)
            keyClick(Qt.Key_Return)
            compare(fixture.commands.length, 1)
            compare(fixture.commands[0][0], 'ask')
            compare(fixture.commands[0][1], 'Explain this error')
        }
        function test_acknowledged_send_clears_only_unchanged_draft() {
            chat.draft = 'Keep this draft'
            click('sendButton')
            verify(chat.submitting)
            chat.focusComposer()
            keyClick(Qt.Key_Return)
            compare(fixture.commands.length, 1)
            chat.commandFinished(fixture.commands[0], false)
            compare(chat.draft, 'Keep this draft')
            verify(!chat.submitting)
            click('sendButton')
            chat.draft = 'A new draft typed while sending'
            chat.commandFinished(fixture.commands[1], true)
            compare(chat.draft, 'A new draft typed while sending')
            click('sendButton')
            chat.commandFinished(fixture.commands[2], true)
            compare(chat.draft, '')
        }
        function test_empty_request_is_not_sent() {
            chat.draft = '  '
            chat.focusComposer()
            keyClick(Qt.Key_Return)
            compare(fixture.commands.length, 0)
        }
        function test_send_button() {
            chat.draft = 'Hello Arvis'
            click('sendButton')
            compare(fixture.commands.length, 1)
            compare(fixture.commands[0][0], 'ask')
            compare(fixture.commands[0][1], 'Hello Arvis')
        }
        function test_escape_closes_without_request() {
            chat.focusComposer()
            keyClick(Qt.Key_Escape)
            compare(fixture.closeRequests, 1)
            compare(fixture.commands.length, 0)
        }
        function test_stop_active_work() {
            state({state: 'working', activity: 'Inspecting the page'})
            click('stopButton')
            compare(fixture.commands[0][0], 'cancel')
        }
        function test_voice_mute_and_new_chat() {
            click('voiceButton')
            compare(fixture.commands[0][0], 'activate')
            click('muteButton')
            compare(fixture.commands[1][0], 'mute')
            click('newChatButton')
            compare(fixture.commands[2][0], 'new-conversation')
        }
        function longHistory() {
            var history = []
            for (var i = 0; i < 30; i++)
                history.push({role: i % 2 ? 'assistant' : 'user', text: 'Message ' + i + '\nSome context and explanation to make this scroll.', time: 1793050000 + i})
            return history
        }
        function atLatest(list) {
            return list.followTail && Math.abs(list.contentY - (list.contentHeight - list.height + list.originY)) <= 1
        }
        function settledConversation(history) {
            state({history: history, state: 'working', stream: 'New answer'})
            var list = target('conversationList')
            tryVerify(function() { return list.contentHeight > list.height * 2 && atLatest(list) })
            waitForPolish(fixture)
            verify(waitForRendering(list))
            // Delegates have varying heights. Wait for their initial layout and
            // the deferred followLatest before measuring the wheel/drag origin.
            tryVerify(function() { return !list.moving && atLatest(list) })
            return list
        }
        function test_scroll_position_survives_stream_updates() {
            var history = longHistory()
            var list = settledConversation(history)
            var end = list.contentY
            mouseWheel(list, list.width / 2, list.height / 2, 0, 480)
            tryVerify(function() { return list.contentY < end - 45 })
            tryVerify(function() { return !list.moving && !list.flicking })
            verify(!list.followTail, 'Scrolling up should pause following the latest response')
            var before = list.contentY
            state({history: history, state: 'working', stream: 'New answer continuing with more details'})
            compare(list.contentY, before)
            click('latestButton')
            tryVerify(function() { return atLatest(list) })
        }
        function test_scrollbar_drag_preserves_reading_during_streaming() {
            var history = longHistory()
            var list = settledConversation(history)
            var scrollbar = target('conversationScrollbar')
            var thumb = scrollbar.contentItem
            var end = list.contentY
            mouseDrag(thumb, thumb.width / 2, thumb.height / 2, 0, -180, Qt.LeftButton)
            tryVerify(function() { return list.contentY < end - 45 && !scrollbar.pressed && !list.moving })
            verify(!list.followTail, 'Dragging the scrollbar up should pause automatic scrolling')
            var before = list.contentY
            var paragraph = 'The assistant continues with a longer explanation while you read an earlier message.\n\n'
            state({history: history, state: 'working', stream: paragraph.repeat(12)})
            waitForPolish(fixture)
            compare(list.contentY, before)
            verify(target('latestButton').visible)
        }
        function test_voice_failure_preserves_manual_capture_fallback() {
            state({voice: 'unavailable', wake_ready: false})
            compare(target('muteButton').label, 'Voice unavailable')
            click('voiceButton')
            compare(fixture.commands[0][0], 'activate')
            state({voice: 'disabled'})
            verify(!target('voiceButton').enabled)
            compare(target('muteButton').label, 'Voice off')
            state({voice: 'starting'})
            verify(!target('voiceButton').enabled)
            compare(target('muteButton').label, 'Starting voice…')
            state({voice: 'idle', wake_ready: false})
            verify(target('voiceButton').enabled)
            compare(target('muteButton').label, 'Voice ready')
        }
        function test_small_panel_keeps_composer_visible() {
            fixture.width = 460
            fixture.height = 550
            state({history: [{role: 'user', text: 'Check the preview.', time: 1793050000}], state: 'needs_input', pending: [{id: 'compact', details: {reason: 'Run the build check?', command: 'npm run build'}}]})
            var composer = target('composerInput')
            var point = composer.mapToItem(fixture, 0, 0)
            verify(point.y >= 0 && point.y + composer.height <= fixture.height)
            var stop = target('stopButton')
            point = stop.mapToItem(fixture, 0, 0)
            verify(point.y >= 0 && point.y + stop.height <= fixture.height)
        }
        function question(id) {
            return {id: id, details: {questions: [{question: 'Which project should I use?'}]}}
        }
        function typeAnswer(text) {
            var input = target('approvalInput')
            input.forceActiveFocus()
            for (var character of text) keyClick(character)
            compare(input.text, text)
            compare(chat.answerDraft, text)
        }
        function test_approval_answer_survives_heartbeat_and_failed_reply() {
            state({state: 'needs_input', pending: [question('first')]})
            typeAnswer('Arvis')
            state({state: 'needs_input', pending: [question('first')], activity: 'Waiting for input', updated_at: 1793050001})
            compare(target('approvalInput').text, 'Arvis')
            click('approvalConfirm')
            compare(fixture.commands[0].join('|'), 'respond|first|answer|Arvis')
            chat.commandFinished(fixture.commands[0], false)
            compare(target('approvalInput').text, 'Arvis')
            chat.commandFinished(fixture.commands[0], true)
            compare(target('approvalInput').text, '')
        }
        function test_new_approval_resets_answer_and_ignores_stale_ack() {
            state({state: 'needs_input', pending: [question('first')]})
            typeAnswer('Arvis')
            click('approvalConfirm')
            state({state: 'needs_input', pending: [question('second')]})
            compare(target('approvalInput').text, '')
            typeAnswer('Other project')
            chat.commandFinished(fixture.commands[0], true)
            compare(target('approvalInput').text, 'Other project')
        }
        function test_busy_composer_queues_followup_and_keeps_stop_available() {
            state({state: 'working', activity: 'Inspecting the page', queued: 1})
            chat.draft = 'Check the mobile layout too'
            verify(target('stopButton').visible)
            verify(!target('newChatButton').enabled)
            click('sendButton')
            compare(fixture.commands[0].join('|'), 'ask|Check the mobile layout too')
            verify(target('stopButton').visible)
        }
        function test_offline_composer_does_not_dispatch() {
            chat.online = false
            chat.draft = 'Wait until connected'
            chat.focusComposer()
            keyClick(Qt.Key_Return)
            compare(fixture.commands.length, 0)
            verify(!target('sendButton').enabled)
        }
    }
}
