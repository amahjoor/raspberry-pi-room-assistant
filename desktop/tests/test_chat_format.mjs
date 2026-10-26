import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const path = fileURLToPath(new URL('../omarchy/arvis.desktop/ChatFormat.js', import.meta.url));
const source = readFileSync(path, 'utf8').replace(/^\.pragma library\s*\n/, '');
const context = vm.createContext({});
vm.runInContext(source, context, {filename: path, timeout: 1000});
const render = text => context.richText(text, '#eeeeee', '#242424', '#aaccee');

test('paragraphs, line breaks, bold, inline code, and escapes remain readable', () => {
  const html = render('Hello **Arvis**.\nUse `x < y && y > 0`.\n\nLiteral \\* and <tag>.');
  assert.match(html, /Hello <b>Arvis<\/b>\.<br>Use <code[^>]*>x &lt; y &amp;&amp; y &gt; 0<\/code>/);
  assert.match(html, /Literal \* and &lt;tag&gt;\./);
  assert.equal((html.match(/<p /g) || []).length, 2);
});

test('fenced code preserves whitespace and never parses embedded markdown or HTML', () => {
  const html = render('```js\n  if (a < b) {\n\tprint("**literal** <img src=file:///secret>");\n  }\n```');
  assert.match(html, /<pre[^>]*>  if \(a &lt; b\) \{\n\tprint\(&quot;\*\*literal\*\* &lt;img src=file:\/\/\/secret&gt;&quot;\);\n  \}<\/pre>/);
  assert.doesNotMatch(html, /<img|<b>|<a /);
  assert.match(render('~~~\nunfinished\n  code'), /<pre[^>]*>unfinished\n  code<\/pre>/);
});

test('lists, numbered starts, headings, and quotes have semantic structure', () => {
  const html = render('## Notes\n- one\n- **two**\n\n3. third\n4. fourth\n\n> a quote\n> next line');
  assert.match(html, /<b>Notes<\/b>/);
  assert.match(html, /<ul[^>]*><li>one<\/li><li><b>two<\/b><\/li><\/ul>/);
  assert.match(html, /<ol start="3"[^>]*><li>third<\/li><li>fourth<\/li><\/ol>/);
  assert.match(html, /<blockquote[^>]*>a quote<br>next line<\/blockquote>/);
});

test('only explicit HTTP(S) links become anchors and attributes are escaped', () => {
  const html = render('[Docs](https://example.org/page?q=a&lang=en) [Local](file:///etc/passwd) [JS](javascript:alert(1)) [Data](data:text/html,no) [Relative](/private)');
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.match(html, /href="https:\/\/example.org\/page\?q=a&amp;lang=en"/);
  assert.match(html, /\[Local\]\(file:\/\/\/etc\/passwd\)/);
  assert.match(render('[Path](https://example.org/wiki/Thing_(name))'), /href="https:\/\/example.org\/wiki\/Thing_\(name\)"/);
  for (const url of ['https://', 'https://x.test/" onclick="bad', 'https://x.test/\nsecret', 'https://x.test/\u0000']) {
    assert.doesNotMatch(render(`[unsafe](${url})`), /<a /);
  }
});

test('image and raw HTML payloads never create resource-loading elements', () => {
  const html = render('![network](https://example.org/track) ![local](file:///private.png) ![reference][img]\n\n[img]: https://example.org/track\n\n<img src="https://example.org/track"><style>@import "https://example.org/style";</style><script>bad()</script>');
  assert.doesNotMatch(html, /<(?:img|style|script|iframe|object|link)\b|<a /i);
  assert.match(html, /&lt;img src=&quot;https:\/\/example.org\/track&quot;&gt;/);
  assert.match(html, /!\[network\]\(https:\/\/example.org\/track\)/);
});

test('generated code and link markup is never processed as fresh markdown', () => {
  const html = render('`**code** [x](https://example.org)` [<img src=x> **label**](https://example.org)');
  assert.doesNotMatch(html, /<b>|<img/);
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.match(html, /&lt;img src=x&gt; \*\*label\*\*/);
  assert.match(html, /<code[^>]*>\*\*code\*\* \[x\]\(https:\/\/example.org\)<\/code>/);
});

test('colors cannot inject CSS or HTML and large messages are bounded', () => {
  const html = context.richText('hello', 'red; background:url(https://evil)', '"><img src=x>', 'blue" onclick="bad');
  assert.doesNotMatch(html, /evil|<img|onclick|background:url/);
  assert.match(html, /color:#e6e6e6/);
  const large = render('a'.repeat(50000));
  assert.match(large, /Message truncated for display/);
  assert.ok(large.length < 40500);
  assert.equal(large.includes('a'.repeat(40001)), false);
});

test('pathological brackets and delimiters finish with bounded output', () => {
  context.input = '['.repeat(39000) + '](file:///secret)';
  const html = vm.runInContext('richText(input)', context, {timeout: 1000});
  assert.doesNotMatch(html, /<a /);
  assert.ok(html.length < 41000);
  assert.doesNotThrow(() => render('`` `** __ '.repeat(2000)));
});
