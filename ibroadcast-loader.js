(() => {
    const script = document.createElement("script");
    const url = new URL("browse/pics/ibroadcast-playback.js?v=3", document.currentScript.src).href;
    const policy = window.trustedTypes?.createPolicy("ibroadcast-player", {
        createScriptURL(candidate) {
            if (candidate !== url) {
                throw new TypeError("Unexpected iBroadcast script URL");
            }
            return url;
        }
    });
    script.src = policy ? policy.createScriptURL(url) : url;
    document.head.append(script);
})();
