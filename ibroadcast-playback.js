(() => {
    const fields = document.getElementById("modalScriptExecArgumentsList");
    if (fields) {
        // myMPD's script dialog does not support disabled select options.
        const disableEmptyTags = () => {
            const option = fields.querySelector('select[name="browse"] option[value="tag_unavailable"]');
            if (option) option.disabled = true;
        };
        new MutationObserver(disableEmptyTags).observe(fields, {childList: true, subtree: true});
        disableEmptyTags();
    }

    const anchor = document.getElementById("PlaybackTitle");
    if (!anchor || document.getElementById("ibroadcast-picker")) {
        return;
    }
    const button = document.createElement("button");
    button.id = "ibroadcast-picker";
    button.type = "button";
    button.className = "btn btn-secondary me-2 mb-2";
    const icon = document.createElement("img");
    icon.src = new URL("ibroadcast.png", document.currentScript.src).href;
    icon.alt = "";
    icon.width = 24;
    icon.height = 24;
    icon.className = "me-2";
    button.append(icon, "iBroadcast");
    button.addEventListener("click", () => execScriptFromOptions("iBroadcast", [""]));
    anchor.after(button);
})();
