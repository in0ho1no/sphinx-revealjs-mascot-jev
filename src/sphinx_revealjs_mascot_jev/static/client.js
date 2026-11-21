// 発表者の発話を音声認識でテキストにし、発話の区切りごとに /__mascot_jev__/decide で判定して、マスコットにリアクションさせる。
(() => {
    "use strict";

    const CONFIG_ID = "sphinx-revealjs-mascot-jev-config";
    const DECIDE_URL = "/__mascot_jev__/decide";
    const LISTENING_ATTRIBUTE = "data-speech-listening";
    const SILENCE_MS = 1500;
    const MIN_CHARS = 8;
    const MAX_UTTERANCE_CHARS = 120;
    const MAX_TITLE_CHARS = 60;
    const SLIDE_GUARD_MS = 4000;
    const REQUEST_TIMEOUT_MS = 800;
    const RESTART_DELAY_MS = 1000;
    // global は発火後に判定を止める時間、repeat は同じアクションを再び発火できるまでの時間
    const COOLDOWNS = Object.freeze({
        tilt: { global: 12000, repeat: 25000 },
        trouble: { global: 15000, repeat: 30000 },
        proud: { global: 15000, repeat: 30000 },
        happy: { global: 15000, repeat: 30000 },
        surprise: { global: 15000, repeat: 35000 },
    });
    const FATAL_ERRORS = new Set(["not-allowed", "service-not-allowed", "language-not-supported"]);
    // 無音での終了と、OFF にしたときの中止で起きるエラー
    const EXPECTED_ERRORS = new Set(["no-speech", "aborted"]);

    function debug(...args) {
        console.debug("[mascot-jev]", ...args);
    }

    // 文字数はサーバー側（Python の str）と同じく、コードポイントで数える
    function chars(text) {
        return Array.from(text);
    }

    function readConfig() {
        const element = document.getElementById(CONFIG_ID);
        if (!element) return null;
        try {
            const config = JSON.parse(element.textContent);
            if (typeof config?.lang === "string" && typeof config?.toggleKey === "string" && config.toggleKey) return config;
        } catch (error) {
            debug("invalid config:", error);
        }
        return null;
    }

    function slideTitle(deck) {
        const heading = deck.getCurrentSlide()?.querySelector("h1, h2, h3");
        return heading ? chars(heading.textContent.trim()).slice(0, MAX_TITLE_CHARS).join("") : "";
    }

    function isEditable(target) {
        return Boolean(target?.isContentEditable || target?.closest?.("input, textarea, select"));
    }

    function startClient(Recognition, deck, mascot, config) {
        const recognition = new Recognition();
        recognition.lang = config.lang;
        recognition.continuous = true;
        recognition.interimResults = true;

        const toggleKey = config.toggleKey.toLowerCase();
        const lastFired = new Map();
        let listening = false;
        // マイクの ON・OFF のたびに進め、切り替えをまたいだ応答を見分ける
        let generation = 0;
        // start() から end イベントまでの間は真。この間の start() は InvalidStateError になる
        let running = false;
        // 実行中の認識を開始したときの generation。OFF の前に開始した認識から遅れて届く結果を取り込まないために使う
        let sessionGeneration = 0;
        let lastError = null;
        let restartTimer = null;
        let silenceTimer = null;
        let request = null;
        let guardUntil = -Infinity;
        let cooldownUntil = -Infinity;
        // 区切りの途中で認識が終了・再開しても発話を失わないよう、終了したセッションの分を carriedText に移す
        let carriedText = "";
        let sessionText = "";
        let lastResults = [];
        // 現在の区切りは、結果の segmentStart 番目以降の連結から、consumedText と先頭から一致する部分を除いたもの。
        // 区切りの時点で未確定だった結果は、後から同じ位置で書き換わる。位置だけで除くと、その後に伸びた発話を失う
        let segmentStart = 0;
        let consumedText = "";

        function transcriptFrom(results, start) {
            let text = "";
            for (let index = start; index < results.length; index += 1) text += results[index][0].transcript;
            return text;
        }

        // 文字数だけで除くと、未確定の結果が別の発話に書き換わったときに、新しい発話の先頭を落とす。
        // 除いた時点の文字列と一致する部分だけを除き、食い違った位置から後は新しい発話として扱う
        function withoutConsumed(text) {
            const textChars = chars(text);
            const consumedChars = chars(consumedText);
            let common = 0;
            while (common < consumedChars.length && common < textChars.length && textChars[common] === consumedChars[common]) common += 1;
            return textChars.slice(common).join("");
        }

        function clearSegment() {
            clearTimeout(silenceTimer);
            silenceTimer = null;
            carriedText = "";
            sessionText = "";
            // 確定した結果は変わらないため位置で除き、未確定の結果はその時点の文字列を除く
            segmentStart = 0;
            while (segmentStart < lastResults.length && lastResults[segmentStart].isFinal) segmentStart += 1;
            consumedText = transcriptFrom(lastResults, segmentStart);
        }

        function beginSession() {
            restartTimer = null;
            lastError = null;
            try {
                recognition.start();
                running = true;
                sessionGeneration = generation;
            } catch (error) {
                debug("speech recognition could not start:", error);
                setListening(false);
            }
        }

        function setListening(on) {
            if (listening === on) return;
            listening = on;
            generation += 1;
            clearSegment();
            request?.abort();
            request = null;
            clearTimeout(restartTimer);
            restartTimer = null;
            const chara = document.getElementById("chara");
            if (on) {
                chara?.setAttribute(LISTENING_ATTRIBUTE, "true");
                // 直前の OFF で中止したセッションが終わっていなければ、end イベントで再開する
                if (!running) beginSession();
            } else {
                chara?.removeAttribute(LISTENING_ATTRIBUTE);
                if (running) recognition.abort();
            }
        }

        function isCoolingDown(now) {
            return now < guardUntil || now < cooldownUntil;
        }

        function endSegment() {
            const text = (carriedText + sessionText).trim();
            clearSegment();
            if (!listening || isCoolingDown(performance.now()) || !mascot.isVisible()) return;
            if (chars(text.replace(/\s/gu, "")).length < MIN_CHARS) return;
            requestDecision({
                slide_title: slideTitle(deck),
                utterance: chars(text).slice(-MAX_UTTERANCE_CHARS).join(""),
                available_actions: mascot.getAvailableActions(),
            });
        }

        async function requestDecision(body) {
            const controller = new AbortController();
            const sentGeneration = generation;
            const sentAt = performance.now();
            request = controller;
            const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
            try {
                const response = await fetch(DECIDE_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                    signal: controller.signal,
                });
                if (response.status !== 200) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                // 中止のタイマーは実行が遅れうるため、中止されていなくても経過時間で締切を確かめる
                if (controller.signal.aborted || performance.now() - sentAt > REQUEST_TIMEOUT_MS) return;
                if (sentGeneration !== generation || !listening) return;
                react(data?.action, body.available_actions);
            } catch (error) {
                debug("decide request failed:", error);
            } finally {
                clearTimeout(timer);
                if (request === controller) request = null;
            }
        }

        function react(action, sentActions) {
            if (typeof action !== "string" || !Object.hasOwn(COOLDOWNS, action) || !sentActions.includes(action)) return;
            const now = performance.now();
            if (isCoolingDown(now) || now - (lastFired.get(action) ?? -Infinity) < COOLDOWNS[action].repeat) return;
            if (!mascot.isVisible()) return;
            mascot.trigger(action);
            lastFired.set(action, now);
            cooldownUntil = now + COOLDOWNS[action].global;
        }

        function pauseForSlide() {
            clearSegment();
            guardUntil = performance.now() + SLIDE_GUARD_MS;
        }

        recognition.addEventListener("result", (event) => {
            if (!listening || sessionGeneration !== generation) return;
            lastResults = event.results;
            if (!mascot.isVisible()) {
                clearSegment();
                return;
            }
            // 照合だけに任せると、取り消された結果と同じ文言の別の発話を、除いた発話と取り違える
            if (lastResults.length <= segmentStart) consumedText = "";
            sessionText = withoutConsumed(transcriptFrom(lastResults, segmentStart));
            clearTimeout(silenceTimer);
            silenceTimer = setTimeout(endSegment, SILENCE_MS);
        });

        recognition.addEventListener("error", (event) => {
            lastError = event.error;
            if (!EXPECTED_ERRORS.has(event.error)) debug("speech recognition error:", event.error);
            if (FATAL_ERRORS.has(event.error)) setListening(false);
        });

        recognition.addEventListener("end", () => {
            running = false;
            carriedText += sessionText;
            sessionText = "";
            lastResults = [];
            segmentStart = 0;
            consumedText = "";
            const error = lastError;
            lastError = null;
            if (!listening) return;
            if (error && !EXPECTED_ERRORS.has(error)) {
                restartTimer = setTimeout(beginSession, RESTART_DELAY_MS);
            } else {
                beginSession();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.altKey || event.ctrlKey || event.metaKey || event.repeat || event.isComposing) return;
            if (typeof event.key !== "string" || event.key.toLowerCase() !== toggleKey || isEditable(event.target)) return;
            event.preventDefault();
            setListening(!listening);
        });

        deck.on("slidechanged", pauseForSlide);
        deck.on("fragmentshown", pauseForSlide);
    }

    if (window.self !== window.parent) return;
    if (new URLSearchParams(window.location.search).has("receiver")) return;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) return;
    const deck = window.Reveal;
    if (!deck) return;
    const config = readConfig();
    if (!config) {
        debug("config not found");
        return;
    }
    if (window.RevealMascot) {
        startClient(Recognition, deck, window.RevealMascot, config);
    } else {
        // Reveal.isReady() は ready イベントの前に真になり、mascot.js が ready を待つ間の判定で RevealMascot を見落とすため使わない
        deck.on("ready", () => {
            if (window.RevealMascot) startClient(Recognition, deck, window.RevealMascot, config);
        });
    }
})();
