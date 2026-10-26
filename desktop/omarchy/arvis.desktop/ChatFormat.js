.pragma library

// A small, predictable Markdown subset for chat text. Build HTML only from
// escaped source tokens; never run replacements over already-generated HTML.
// Images and raw HTML remain text, so a message cannot fetch document resources.
var MAX_TEXT = 40000

function escapeHtml(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;")
}

function safeColor(value, fallback) {
  var color = String(value || "")
  return /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(color) ? color : fallback
}

function webUrl(value) {
  return /^https?:\/\/[^\s\/<>"'?#]+(?:[\/?#][^\s<>"']*)?$/i.test(value)
    && !/[\x00-\x1f\x7f]/.test(value)
}

function inlineText(source, colors, depth) {
  var output = []
  var nextBracket = -2
  var noMoreTicks = false
  var noMoreStars = false
  var noMoreUnderscores = false
  for (var i = 0; i < source.length;) {
    var character = source.charAt(i)
    if (character === "\\" && i + 1 < source.length && /[\\`*_{}\[\]()#+.!>~-]/.test(source.charAt(i + 1))) {
      output.push(escapeHtml(source.charAt(i + 1)))
      i += 2
      continue
    }
    if (character === "`" && !noMoreTicks) {
      var count = 1
      while (source.charAt(i + count) === "`") count++
      var marker = source.slice(i, i + count)
      var end = source.indexOf(marker, i + count)
      // Delimiters must have the same length, not just match part of a run.
      while (end >= 0 && (source.charAt(end - 1) === "`" || source.charAt(end + count) === "`"))
        end = source.indexOf(marker, end + count)
      if (end >= 0) {
        output.push('<code style="background-color:' + colors.code + ';">' + escapeHtml(source.slice(i + count, end)) + '</code>')
        i = end + count
        continue
      }
      // A run with no later backticks cannot start any following code span.
      noMoreTicks = source.indexOf("`", i + count) < 0
      output.push(marker)
      i += count
      continue
    }
    var bold = source.slice(i, i + 2)
    if (depth < 4 && ((bold === "**" && !noMoreStars) || (bold === "__" && !noMoreUnderscores))) {
      var closing = source.indexOf(bold, i + 2)
      if (closing > i + 2) {
        output.push('<b>' + inlineText(source.slice(i + 2, closing), colors, depth + 1) + '</b>')
        i = closing + 2
        continue
      }
      if (closing < 0) {
        if (bold === "**") noMoreStars = true
        else noMoreUnderscores = true
      }
    }
    if (character === "[" && source.charAt(i - 1) !== "!" && depth < 4) {
      if (nextBracket === -2 || (nextBracket >= 0 && nextBracket <= i)) nextBracket = source.indexOf("]", i + 1)
      if (nextBracket > i && nextBracket - i <= 1024 && source.charAt(nextBracket + 1) === "(") {
        var startUrl = nextBracket + 2
        var endUrl = startUrl
        var balance = 1
        while (endUrl < source.length && endUrl - startUrl <= 2048 && balance > 0) {
          var urlCharacter = source.charAt(endUrl)
          if (urlCharacter === "(") balance++
          else if (urlCharacter === ")") balance--
          if (balance > 0) endUrl++
        }
        var url = source.slice(startUrl, endUrl)
        if (balance === 0 && webUrl(url)) {
          // A label is escaped as text, avoiding nested anchors or markup.
          output.push('<a href="' + escapeHtml(url) + '" style="color:' + colors.link + ';">'
            + escapeHtml(source.slice(i + 1, nextBracket)) + '</a>')
          i = endUrl + 1
          continue
        }
      }
    }
    output.push(character === "\n" ? "<br>" : escapeHtml(character))
    i++
  }
  return output.join("")
}

function fence(line) {
  return /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line)
}

function listItem(line) {
  var unordered = /^ {0,3}[-+*][ \t]+(.*)$/.exec(line)
  if (unordered) return {kind: "ul", text: unordered[1], start: 1}
  var ordered = /^ {0,3}(\d{1,9})[.)][ \t]+(.*)$/.exec(line)
  return ordered ? {kind: "ol", text: ordered[2], start: Number(ordered[1])} : null
}

function specialLine(line) {
  return fence(line) || listItem(line) || /^ {0,3}>/.test(line) || /^ {0,3}#{1,4}[ \t]+/.test(line)
}

function richText(text, foregroundColor, codeBackgroundColor, linkColor) {
  var raw = String(text === undefined || text === null ? "" : text)
  var truncated = raw.length > MAX_TEXT
  var source = raw.slice(0, MAX_TEXT).replace(/\r\n?/g, "\n")
  var colors = {foreground: safeColor(foregroundColor, "#e6e6e6"),
    code: safeColor(codeBackgroundColor, "#20202a"), link: safeColor(linkColor, "#8bb9f0")}
  var lines = source.split("\n")
  var blocks = []
  for (var i = 0; i < lines.length;) {
    var line = lines[i]
    if (!line.trim()) { i++; continue }
    var opening = fence(line)
    if (opening) {
      var code = []
      var closeFence = new RegExp("^ {0,3}" + opening[1].charAt(0) + "{" + opening[1].length + ",}[ \\t]*$")
      i++
      while (i < lines.length && !closeFence.test(lines[i])) code.push(lines[i++])
      if (i < lines.length) i++
      blocks.push('<pre style="background-color:' + colors.code + '; margin:8px 0; white-space:pre-wrap;">'
        + escapeHtml(code.join("\n")) + '</pre>')
      continue
    }
    var item = listItem(line)
    if (item) {
      var kind = item.kind
      var entries = []
      var number = item.start
      while (i < lines.length && (item = listItem(lines[i])) && item.kind === kind) {
        entries.push('<li>' + inlineText(item.text, colors, 0) + '</li>')
        i++
      }
      blocks.push('<' + kind + (kind === "ol" ? ' start="' + number + '"' : '')
        + ' style="margin:6px 0;">' + entries.join("") + '</' + kind + '>')
      continue
    }
    if (/^ {0,3}>/.test(line)) {
      var quote = []
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) quote.push(lines[i++].replace(/^ {0,3}>[ \t]?/, ""))
      blocks.push('<blockquote style="margin:8px 0 8px 12px;">' + inlineText(quote.join("\n"), colors, 0) + '</blockquote>')
      continue
    }
    var heading = /^ {0,3}(#{1,4})[ \t]+(.*)$/.exec(line)
    if (heading) {
      blocks.push('<p style="margin:10px 0 6px 0;"><b>' + inlineText(heading[2], colors, 0) + '</b></p>')
      i++
      continue
    }
    var paragraph = [line]
    i++
    while (i < lines.length && lines[i].trim() && !specialLine(lines[i])) paragraph.push(lines[i++])
    blocks.push('<p style="margin:0 0 10px 0;">' + inlineText(paragraph.join("\n"), colors, 0) + '</p>')
  }
  if (truncated) blocks.push('<p><i>Message truncated for display.</i></p>')
  return '<div style="color:' + colors.foreground + ';">' + blocks.join("") + '</div>'
}
