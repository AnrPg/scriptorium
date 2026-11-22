// The ONE maths configuration (vendored KaTeX + mhchem): used by the app (preview, inspector) and by the PDF
// export (pipeline.html_preview.document_html), so a formula renders identically everywhere.
window.MATH_DELIMITERS = [{ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false }];
window.renderMath = function (el) {
  try { renderMathInElement(el, { delimiters: window.MATH_DELIMITERS, throwOnError: false, strict: 'ignore' }); } catch (e) { /* text stays */ }
};
