(function () {
  var img =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6aAAAAAElFTkSuQmCC";
  var root = document.getElementById("root");
  if (!root) return;
  root.innerHTML =
    '<div class="QuestionPage">' +
    '<h1 class="QuestionHeader-title">知乎压力测试问题</h1>' +
    '<div class="AnswerItem">' +
    '<div class="AuthorInfo-name">答主甲</div>' +
    '<div class="RichContent-inner"><div class="RichText ztext">' +
    "<p>这是用于 TechVault Chromium 连续捕获压力测试的知乎风格正文。</p>" +
    "<p>需要足够长度以通过自动捕获门槛，并验证 TaskBridge 任务终态与窗口回收。</p>" +
    "<p>归档稳定性验证。归档稳定性验证。归档稳定性验证。归档稳定性验证。</p>" +
    '<img src="' +
    img +
    '" alt="zimg" />' +
    "</div></div></div></div>";
})();
