// 写真のしぼりこみと、大きく見る画面
const figures = [...document.querySelectorAll(".gallery figure")];
const buttons = document.querySelectorAll(".filters button");

buttons.forEach((b) => {
  b.addEventListener("click", () => {
    buttons.forEach((x) => x.classList.toggle("on", x === b));
    const cat = b.dataset.filter;
    figures.forEach((f) => (f.hidden = cat !== "all" && f.dataset.cat !== cat));
  });
});

const viewer = document.querySelector(".viewer");
const viewImg = viewer.querySelector("img");
const viewCap = viewer.querySelector("figcaption");
let index = 0;

function visible() {
  return figures.filter((f) => !f.hidden);
}

function show(i) {
  const list = visible();
  index = (i + list.length) % list.length;
  const img = list[index].querySelector("img");
  viewImg.src = img.src;
  viewImg.alt = img.alt;
  viewCap.textContent = list[index].querySelector("figcaption").textContent;
}

figures.forEach((f) => {
  f.tabIndex = 0;
  const open = () => {
    show(visible().indexOf(f));
    viewer.hidden = false;
    document.body.style.overflow = "hidden";
  };
  f.addEventListener("click", open);
  f.addEventListener("keydown", (e) => {
    if (e.key === "Enter") open();
  });
});

function close() {
  viewer.hidden = true;
  document.body.style.overflow = "";
}

viewer.querySelector(".close").addEventListener("click", close);
viewer.querySelector(".prev").addEventListener("click", () => show(index - 1));
viewer.querySelector(".next").addEventListener("click", () => show(index + 1));
viewer.addEventListener("click", (e) => {
  if (e.target === viewer) close();
});
document.addEventListener("keydown", (e) => {
  if (viewer.hidden) return;
  if (e.key === "Escape") close();
  if (e.key === "ArrowLeft") show(index - 1);
  if (e.key === "ArrowRight") show(index + 1);
});

// スマホでは左右にスワイプして写真を切りかえる
let touchX = null;
viewer.addEventListener("touchstart", (e) => (touchX = e.touches[0].clientX), { passive: true });
viewer.addEventListener("touchend", (e) => {
  if (touchX === null) return;
  const dx = e.changedTouches[0].clientX - touchX;
  if (Math.abs(dx) > 50) show(index + (dx < 0 ? 1 : -1));
  touchX = null;
});
