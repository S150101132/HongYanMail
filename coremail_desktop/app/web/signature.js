'use strict';
/* 鸿雁 · v0.7 signature pictures (a company logo) and picture sizing. The signature
   keeps its pictures as data: URIs; the composer copies the signature into the body,
   so sending turns them into inline cid: parts like any body image (plain, signed
   and encrypted mail alike). Uses app.js / compose.js globals ($, api, C, S...). */

// Same limits as the backend (SIGNATURE_* in backend.py); the backend has the last word.
const SIG_LIMIT = { images: 5, bytes: 1048576 };
const SIG_TOO_LARGE = '签名最多放 5 张图片，图片合计不超过 1 MB。请换一张小一些的图片。';
const dataBytes = (src) => { const b64 = String(src).split(',')[1] || ''; return Math.floor(b64.length * 3 / 4); };
function sigImages() { return $$('#pref-signature img').filter((i) => i.src.startsWith('data:')); }

function insertSignatureImages(list, range) {
  const editor = $('#pref-signature');
  const images = list.filter(isBodyImage);
  if (list.length > images.length) toast('签名里只能放 PNG、JPEG、GIF、WebP 或 BMP 图片', { error: true });
  if (!images.length) return;
  const have = sigImages();
  const bytes = have.reduce((n, i) => n + dataBytes(i.src), 0) + images.reduce((n, a) => n + (a.size || 0), 0);
  if (have.length + images.length > SIG_LIMIT.images || bytes > SIG_LIMIT.bytes) { toast(SIG_TOO_LARGE, { error: true }); return; }
  editor.focus();
  if (range && editor.contains(range.startContainer)) { const s = getSelection(); s.removeAllRanges(); s.addRange(range); }
  document.execCommand('insertHTML', false, images.map((a) => `<img src="data:${esc(a.type)};base64,${a.data}" alt="${esc(a.name.replace(/\.[^.]+$/, ''))}" data-hy-new="1">`).join(''));
  // A large logo starts at a signature-like width; the size can be changed after.
  $$('img[data-hy-new]', editor).forEach((img) => {
    img.removeAttribute('data-hy-new');
    const fit = () => { if (img.naturalWidth > 360) setImageWidth(img, 240); };
    if (img.complete) fit(); else img.addEventListener('load', fit, { once: true });
  });
}
function signatureRange() {
  const s = getSelection();
  return s.rangeCount && $('#pref-signature').contains(s.anchorNode) ? s.getRangeAt(0).cloneRange() : null;
}
$('#sig-image').onmousedown = (e) => e.preventDefault();
$('#sig-image').onclick = async () => {
  const range = signatureRange();
  try { insertSignatureImages(await api('pickFiles', { images: true }), range); } catch (err) { fail(err); }
};
$('#pref-signature').addEventListener('paste', async (e) => {
  // A copied picture (screenshot, image from a document) goes in as a picture; pasted
  // HTML keeps its text, but pictures that point at the network are dropped below.
  const files = Array.from(e.clipboardData.files || []).filter(isBodyImage);
  const html = e.clipboardData.getData('text/html');
  const textOnly = html ? new DOMParser().parseFromString(html, 'text/html').body.innerText.trim() : '';
  if (files.length && !textOnly) { e.preventDefault(); insertSignatureImages(await Promise.all(files.map(readFile)), signatureRange()); return; }
  setTimeout(dropRemoteImages, 0);
});
$('#pref-signature').addEventListener('drop', async (e) => {
  const files = Array.from(e.dataTransfer.files || []);
  if (!files.length) return;
  e.preventDefault();
  insertSignatureImages(await Promise.all(files.map(readFile)), document.caretRangeFromPoint(e.clientX, e.clientY));
});
// Recipients would have to load a network picture; only local pictures are kept.
function dropRemoteImages() {
  const remote = $$('#pref-signature img').filter((i) => !i.src.startsWith('data:'));
  remote.forEach((i) => i.remove());
  if (remote.length) toast('签名中的网络图片已移除：请用「图片」按钮插入本机图片，收件人不必联网就能看到');
  return remote.length;
}
function signatureHtml() {
  dropRemoteImages();
  hideImageTools();
  return $('#pref-signature').innerHTML.replace(/^(<br>)+$/, '');
}

// ---------------------------------------------------------------- picture size
// Click a picture in the signature or the mail body: a small bar sets its width.
// Width goes on both the attribute and the style, which mail clients read differently.
function setImageWidth(img, width) {
  img.setAttribute('width', String(width)); img.removeAttribute('height');
  img.style.width = width + 'px'; img.style.height = 'auto'; img.style.maxWidth = '100%';
}
const IT = { img: null, editor: null };
function showImageTools(img, editor) {
  IT.img = img; IT.editor = editor;
  const bar = $('#img-tools');
  const natural = img.naturalWidth || img.width || 200;
  const range = $('input', bar);
  range.min = 16; range.max = Math.max(64, Math.min(1200, natural * 2));
  range.value = Math.round(img.getBoundingClientRect().width) || natural;
  $('.w', bar).textContent = range.value + ' px';
  bar.classList.remove('hidden');
  placeImageTools();
}
function placeImageTools() {
  if (!IT.img || !IT.img.isConnected) { hideImageTools(); return; }
  const r = IT.img.getBoundingClientRect(), bar = $('#img-tools');
  const below = r.bottom + 6 + bar.offsetHeight < innerHeight;
  bar.style.left = Math.max(8, Math.min(r.left, innerWidth - bar.offsetWidth - 8)) + 'px';
  bar.style.top = (below ? r.bottom + 6 : Math.max(8, r.top - bar.offsetHeight - 6)) + 'px';
  $$('img.hy-picked').forEach((i) => i !== IT.img && i.classList.remove('hy-picked'));
  IT.img.classList.add('hy-picked');
}
function hideImageTools() {
  $('#img-tools').classList.add('hidden');
  $$('img.hy-picked').forEach((i) => { i.classList.remove('hy-picked'); if (!i.className) i.removeAttribute('class'); });
  IT.img = null;
}
function imageEdited() { if (IT.editor && IT.editor.id === 'cmp-editor') C.dirty = true; }
$('#img-tools input').oninput = (e) => {
  if (!IT.img) return;
  setImageWidth(IT.img, +e.target.value); $('#img-tools .w').textContent = e.target.value + ' px';
  imageEdited(); placeImageTools();
};
$('#img-tools').onmousedown = (e) => { if (e.target.tagName !== 'INPUT') e.preventDefault(); };
$('#img-tools').onclick = (e) => {
  const b = e.target.closest('button'); if (!b || !IT.img) return;
  const img = IT.img;
  if (b.dataset.w === 'del') { hideImageTools(); img.remove(); imageEdited(); return; }
  const width = b.dataset.w === 'orig' ? img.naturalWidth : +b.dataset.w;
  if (width) { setImageWidth(img, Math.min(width, 1200)); $('#img-tools input').value = width; $('#img-tools .w').textContent = width + ' px'; imageEdited(); placeImageTools(); }
};
for (const id of ['pref-signature', 'cmp-editor']) {
  const editor = $('#' + id);
  editor.addEventListener('click', (e) => { if (e.target.tagName === 'IMG') showImageTools(e.target, editor); else hideImageTools(); });
  editor.addEventListener('keydown', () => hideImageTools());
}
document.addEventListener('scroll', () => IT.img && placeImageTools(), true);
document.addEventListener('mousedown', (e) => {
  if (IT.img && !e.target.closest('#img-tools') && e.target !== IT.img) hideImageTools();
}, true);
addEventListener('resize', () => IT.img && placeImageTools());
// The highlight of the picked picture never goes into a draft or a message.
const cleanPicked = (html) => html.replace(/\sclass="hy-picked"/g, '').replace(/(class="[^"]*?)\s*hy-picked/g, '$1');
