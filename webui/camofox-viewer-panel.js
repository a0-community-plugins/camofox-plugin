/**
 * Initialize the draggable/resizable floating panel.
 * Called once when the panel element is first available in the DOM.
 */
export function initPanel(panelEl) {
    const titleBar = panelEl.querySelector(".camofox-title-bar");
    if (!titleBar) return;
    if (panelEl._camofoxInitialized) return; // prevent double-init
    panelEl._camofoxInitialized = true;

    let isDragging = false;
    let dragStartX = 0, dragStartY = 0, panelStartX = 0, panelStartY = 0;
    let isMaximized = false;
    let preMaxW, preMaxH, preMaxX, preMaxY;

    const store = Alpine.store("camofox");

    // Set explicit size first
    const w = store.panelSize.w || 800;
    const h = store.panelSize.h || 600;
    panelEl.style.width = w + "px";
    panelEl.style.height = h + "px";

    // Position: use saved position or default to top-right area
    if (store.panelPosition.x !== null && store.panelPosition.y !== null) {
        panelEl.style.left = store.panelPosition.x + "px";
        panelEl.style.top = store.panelPosition.y + "px";
    } else {
        // Default: right side, vertically centered
        const defaultX = Math.max(20, window.innerWidth - w - 40);
        const defaultY = Math.max(60, Math.floor((window.innerHeight - h) / 2));
        panelEl.style.left = defaultX + "px";
        panelEl.style.top = defaultY + "px";
    }
    // Clear any right/bottom that might conflict
    panelEl.style.right = "auto";
    panelEl.style.bottom = "auto";

    // --- Maximize/Restore button ---
    const controls = titleBar.querySelector(".camofox-controls");
    if (controls) {
        const maxBtn = document.createElement("button");
        maxBtn.title = "Maximize / Restore";
        maxBtn.style.cssText = "background:none;border:none;cursor:pointer;color:inherit;font-size:14px;padding:0 4px;line-height:1;";
        maxBtn.textContent = "⛶";
        controls.insertBefore(maxBtn, controls.firstChild);

        maxBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!isMaximized) {
                // Save current state
                preMaxW = panelEl.offsetWidth;
                preMaxH = panelEl.offsetHeight;
                preMaxX = parseFloat(panelEl.style.left) || 0;
                preMaxY = parseFloat(panelEl.style.top) || 0;
                // Maximize with small margin
                const margin = 8;
                panelEl.style.left = margin + "px";
                panelEl.style.top = margin + "px";
                panelEl.style.width = (window.innerWidth - margin * 2) + "px";
                panelEl.style.height = (window.innerHeight - margin * 2) + "px";
                maxBtn.textContent = "❐";
                maxBtn.title = "Restore";
                isMaximized = true;
            } else {
                // Restore
                panelEl.style.left = preMaxX + "px";
                panelEl.style.top = preMaxY + "px";
                panelEl.style.width = preMaxW + "px";
                panelEl.style.height = preMaxH + "px";
                maxBtn.textContent = "⛶";
                maxBtn.title = "Maximize";
                isMaximized = false;
            }
        });
    }

    // Drag: mousedown on title bar
    titleBar.addEventListener("mousedown", (e) => {
        if (e.target.closest(".camofox-controls")) return;
        if (isMaximized) return; // don't drag when maximized
        isDragging = true;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        const rect = panelEl.getBoundingClientRect();
        panelStartX = rect.left;
        panelStartY = rect.top;
        titleBar.style.cursor = "grabbing";
        e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
        if (!isDragging) return;
        let newX = panelStartX + (e.clientX - dragStartX);
        let newY = panelStartY + (e.clientY - dragStartY);
        newX = Math.max(0, Math.min(newX, window.innerWidth - 100));
        newY = Math.max(0, Math.min(newY, window.innerHeight - 40));
        panelEl.style.left = newX + "px";
        panelEl.style.top = newY + "px";
    });

    document.addEventListener("mouseup", () => {
        if (!isDragging) return;
        isDragging = false;
        titleBar.style.cursor = "grab";
        const rect = panelEl.getBoundingClientRect();
        store.savePosition(rect.left, rect.top);
    });

    // Resize observer — save size when user manually resizes
    const resizeObserver = new ResizeObserver(() => {
        if (!isDragging && !isMaximized) {
            store.saveSize(panelEl.offsetWidth, panelEl.offsetHeight);
        }
    });
    resizeObserver.observe(panelEl);

    // Proportional resize on window resize
    // Track ratio of panel size to viewport at init time
    let vpW = window.innerWidth;
    let vpH = window.innerHeight;

    window.addEventListener("resize", () => {
        if (isMaximized) {
            // Keep maximized panel filling the viewport
            const margin = 8;
            panelEl.style.left = margin + "px";
            panelEl.style.top = margin + "px";
            panelEl.style.width = (window.innerWidth - margin * 2) + "px";
            panelEl.style.height = (window.innerHeight - margin * 2) + "px";
            return;
        }

        const curW = panelEl.offsetWidth;
        const curH = panelEl.offsetHeight;
        const newVpW = window.innerWidth;
        const newVpH = window.innerHeight;

        // Scale panel proportionally with viewport change
        const scaleX = newVpW / vpW;
        const scaleY = newVpH / vpH;
        const scale = Math.min(scaleX, scaleY); // uniform scale
        const newW = Math.max(320, Math.round(curW * scale));
        const newH = Math.max(240, Math.round(curH * scale));
        panelEl.style.width = newW + "px";
        panelEl.style.height = newH + "px";
        store.saveSize(newW, newH);

        // Keep panel in viewport
        const rect = panelEl.getBoundingClientRect();
        if (rect.left + newW > newVpW) {
            panelEl.style.left = Math.max(0, newVpW - newW - 20) + "px";
        }
        if (rect.top + newH > newVpH) {
            panelEl.style.top = Math.max(0, newVpH - newH - 20) + "px";
        }

        vpW = newVpW;
        vpH = newVpH;
    });
}
