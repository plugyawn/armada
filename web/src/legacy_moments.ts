// Small brand moments (design §11): currently the Konami ✦ rain —
// eight arrows and two letters buy a three-second sparkle shower, the
// one sanctioned escape of the ✦ from the header.

const KONAMI_SEQUENCE = [
    "ArrowUp",
    "ArrowUp",
    "ArrowDown",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "ArrowLeft",
    "ArrowRight",
    "b",
    "a",
];
let sequence_progress = 0;
let rain_active = false;

function spawn_sparkle_rain(): void {
    if (rain_active || typeof document === "undefined") {
        return;
    }
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
        return;
    }
    rain_active = true;
    const overlay = document.createElement("div");
    overlay.className = "legacy-sparkle-rain";
    overlay.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 36; index += 1) {
        const drop = document.createElement("span");
        drop.textContent = "✦";
        // Deterministic scatter — no randomness needed for whimsy.
        drop.style.left = `${(index * 61) % 100}%`;
        drop.style.animationDelay = `${(index % 12) * 0.12}s`;
        drop.style.fontSize = `${10 + ((index * 7) % 14)}px`;
        overlay.append(drop);
    }
    document.body.append(overlay);
    setTimeout(() => {
        overlay.remove();
        rain_active = false;
    }, 4600);
}

export function initialize(): void {
    if (typeof document === "undefined" || typeof document.addEventListener !== "function") {
        return;
    }
    document.addEventListener("keydown", (event) => {
        const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
        if (key === KONAMI_SEQUENCE[sequence_progress]) {
            sequence_progress += 1;
        } else {
            sequence_progress = key === KONAMI_SEQUENCE[0] ? 1 : 0;
        }
        if (sequence_progress === KONAMI_SEQUENCE.length) {
            sequence_progress = 0;
            spawn_sparkle_rain();
        }
    });
}
