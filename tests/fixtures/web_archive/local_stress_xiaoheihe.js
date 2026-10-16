(function () {
  var img =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6aAAAAAElFTkSuQmCC";
  var root = document.getElementById("app");
  if (!root) return;
  root.innerHTML =
    '<div class="hb-bbs-link">' +
    '<div class="hb-cpt-page-header hb-bbs-link__header">' +
    "<div>返回</div><div class=\"user-name\">压力测试作者</div><div>关注</div></div>" +
    '<div class="hb-bbs-post"><div class="post__container">' +
    '<div class="link-section-title"><div class="section-title__content">小黑盒压力测试帖子</div></div>' +
    '<div class="post__content hb-article">' +
    "<p>这是用于 TechVault Chromium 连续捕获压力测试的本地正文。</p>" +
    "<p>段落足够长，以便自动捕获逻辑认为正文已就绪，并练习媒体本地化与窗口关闭回收。</p>" +
    "<p>重复内容：归档稳定性验证。归档稳定性验证。归档稳定性验证。</p>" +
    '<img src="' +
    img +
    '" alt="body1" />' +
    '<img src="' +
    img +
    '" alt="body2" />' +
    "</div></div></div></div>";
})();
