const codes = {
  KeyW: "up",
  ArrowUp: "up",
  KeyS: "down",
  ArrowDown: "down",
  KeyA: "left",
  ArrowLeft: "left",
  KeyD: "right",
  ArrowRight: "right",
  Space: "jump",
  KeyE: "interact",
};
export function createInput(pause) {
  const held = new Set();
  let jump = false,
    interact = false;
  function down(k) {
    if (!held.has(k)) {
      if (k === "jump") jump = true;
      if (k === "interact") interact = true;
    }
    held.add(k);
  }
  addEventListener("keydown", (e) => {
    if (e.target.matches("select,input,textarea")) return;
    if (e.code === "Escape" || e.code === "KeyP") {
      e.preventDefault();
      if (!e.repeat) pause();
      return;
    }
    const k = codes[e.code];
    if (k) {
      e.preventDefault();
      down(k);
    }
  });
  addEventListener("keyup", (e) => held.delete(codes[e.code]));
  const buttons = [...document.querySelectorAll("[data-k]")];
  for (const b of buttons) {
    b.onpointerdown = (e) => {
      e.preventDefault();
      b.setPointerCapture(e.pointerId);
      down(b.dataset.k);
      b.classList.add("held");
    };
    for (const event of ["pointerup", "pointercancel", "lostpointercapture"])
      b.addEventListener(event, () => {
        held.delete(b.dataset.k);
        b.classList.remove("held");
      });
  }
  return {
    axis: () => ({
      x: +held.has("right") - held.has("left"),
      z: +held.has("down") - held.has("up"),
    }),
    takeJump: () => {
      const v = jump;
      jump = false;
      return v;
    },
    takeInteract: () => {
      const v = interact;
      interact = false;
      return v;
    },
    clear() {
      held.clear();
      jump = interact = false;
      buttons.forEach((b) => b.classList.remove("held"));
    },
  };
}
