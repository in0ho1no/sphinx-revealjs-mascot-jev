"use strict";
// client.js を、ブラウザの代わりに node:vm のコンテキストで実行するための偽物。
// SpeechRecognition・Reveal・RevealMascot・fetch・タイマーを、テストから操作できる形で用意する。

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CLIENT_PATH = path.join(__dirname, "..", "..", "sphinx_revealjs_mascot_jev", "static", "client.js");
const CLIENT_SOURCE = fs.readFileSync(CLIENT_PATH, "utf8");

const FULL_ACTIONS = Object.freeze(["idle", "jump", "wink", "surprise", "happy", "tilt", "trouble", "proud"]);
const REQUIRED4_ACTIONS = Object.freeze(["idle", "jump", "wink", "surprise"]);

// 保留中のマイクロタスク（Promise の続き）をすべて実行させる
function flush() {
    return new Promise((resolve) => setImmediate(resolve));
}

class FakeClock {
    constructor() {
        this.now = 0;
        this.timers = new Map();
        this.nextId = 1;
    }

    setTimeout(callback, delay = 0) {
        const id = this.nextId;
        this.nextId += 1;
        this.timers.set(id, { at: this.now + Math.max(0, Number(delay) || 0), callback });
        return id;
    }

    clearTimeout(id) {
        this.timers.delete(id);
    }

    async advance(ms) {
        const end = this.now + ms;
        for (;;) {
            let nextId = null;
            let next = null;
            for (const [id, timer] of this.timers) {
                if (timer.at <= end && (next === null || timer.at < next.at)) {
                    nextId = id;
                    next = timer;
                }
            }
            if (next === null) break;
            this.timers.delete(nextId);
            this.now = next.at;
            next.callback();
            await flush();
        }
        this.now = end;
        await flush();
    }
}

class FakeElement {
    constructor(tagName, { textContent = "", isContentEditable = false, parent = null, children = [] } = {}) {
        this.tagName = tagName.toUpperCase();
        this.textContent = textContent;
        this.isContentEditable = isContentEditable;
        this.parent = parent;
        this.children = children;
        this.attributes = new Map();
    }

    setAttribute(name, value) {
        this.attributes.set(name, String(value));
    }

    removeAttribute(name) {
        this.attributes.delete(name);
    }

    getAttribute(name) {
        return this.attributes.has(name) ? this.attributes.get(name) : null;
    }

    // タグ名だけからなるセレクターの並びに対応する
    matches(selectors) {
        return selectors.split(",").some((selector) => selector.trim().toUpperCase() === this.tagName);
    }

    closest(selectors) {
        for (let element = this; element; element = element.parent) {
            if (element.matches(selectors)) return element;
        }
        return null;
    }

    querySelector(selectors) {
        for (const child of this.children) {
            if (child.matches(selectors)) return child;
            const found = child.querySelector(selectors);
            if (found) return found;
        }
        return null;
    }
}

// children は [タグ名, テキスト] か [タグ名, テキスト, 子の配列] の配列
function createSlide(children = []) {
    const build = ([tagName, textContent, grandChildren = []]) =>
        new FakeElement(tagName, { textContent, children: grandChildren.map(build) });
    return new FakeElement("section", { children: children.map(build) });
}

class FakeRecognitionBase extends EventTarget {
    constructor(browser) {
        super();
        this.browser = browser;
        this.lang = "";
        this.continuous = false;
        this.interimResults = false;
        this.running = false;
        this.ending = false;
        this.startCount = 0;
        this.abortCount = 0;
        this.results = [];
        browser.recognitions.push(this);
    }

    start() {
        if (this.browser.startError) throw this.browser.startError;
        // 本物と同じく、終了前の start() は例外になる
        if (this.running) throw new DOMException("recognition has already started", "InvalidStateError");
        this.running = true;
        this.startCount += 1;
        this.results = [];
    }

    abort() {
        this.abortCount += 1;
        if (!this.running || this.ending) return;
        this.ending = true;
        // 本物と同じく、中止の error と end は後から非同期に届く
        this.browser.clock.setTimeout(() => this.fail("aborted"), 0);
    }

    // 認識結果を1件届ける。直前の結果が未確定なら、それを置き換える
    hear(transcript, { isFinal = true } = {}) {
        if (!this.running) throw new Error("recognition is not running");
        const last = this.results.at(-1);
        const index = last && !last.isFinal ? this.results.length - 1 : this.results.length;
        const results = [...this.results];
        results[index] = Object.assign([{ transcript, confidence: 0.9 }], { isFinal });
        this.deliver(results, index);
    }

    // 認識結果の一覧を、そのまま result イベントで届ける
    deliver(results, resultIndex = 0) {
        if (!this.running) throw new Error("recognition is not running");
        this.results = results;
        const event = new Event("result");
        event.resultIndex = resultIndex;
        event.results = [...results];
        this.dispatchEvent(event);
    }

    // error に続けて end を届ける。error のリスナーが呼ぶ abort() は、終了処理中のため何もしない
    fail(error) {
        this.ending = true;
        this.dispatchEvent(Object.assign(new Event("error"), { error }));
        this.end();
    }

    end() {
        this.running = false;
        this.ending = false;
        this.dispatchEvent(new Event("end"));
    }
}

function createFetch(browser) {
    return (url, options = {}) =>
        new Promise((resolve, reject) => {
            const request = {
                url,
                method: options.method,
                headers: options.headers,
                body: JSON.parse(options.body),
                signal: options.signal,
                settled: false,
                at: browser.clock.now,
            };
            const settle = (callback) => {
                if (request.settled) return;
                request.settled = true;
                callback();
            };
            if (browser.honorAbort) {
                options.signal?.addEventListener("abort", () => settle(() => reject(new DOMException("The operation was aborted.", "AbortError"))));
            }
            request.respond = (data, status = 200) => settle(() => resolve({ status, ok: status >= 200 && status < 300, json: async () => data }));
            request.fail = () => settle(() => reject(new TypeError("Failed to fetch")));
            browser.requests.push(request);
        });
}

/**
 * client.js を読み込んだ偽のブラウザを作る。
 *
 * @param {object} [options]
 * @param {string} [options.search] location.search
 * @param {boolean} [options.inFrame] iframe 内での実行とするか
 * @param {"standard" | "webkit" | "none"} [options.recognition] 用意する SpeechRecognition のコンストラクター名
 * @param {boolean} [options.reveal] window.Reveal を用意するか
 * @param {"immediate" | "on-ready" | "never"} [options.mascot] RevealMascot を定義する時点
 * @param {string | null} [options.configText] 注入される設定の JSON。null なら設定の要素を置かない
 * @param {readonly string[]} [options.actions] getAvailableActions() の戻り値
 * @param {boolean} [options.honorAbort] fetch が AbortSignal で中止されるか
 */
function createBrowser(options = {}) {
    const {
        search = "",
        inFrame = false,
        recognition = "standard",
        reveal = true,
        mascot = "immediate",
        configText = JSON.stringify({ lang: "ja-JP", toggleKey: "r" }),
        actions = FULL_ACTIONS,
        honorAbort = true,
    } = options;

    const clock = new FakeClock();
    const browser = {
        clock,
        recognitions: [],
        requests: [],
        triggers: [],
        debugLogs: [],
        consoleProblems: [],
        revealListeners: [],
        documentListeners: [],
        isReadyCalls: 0,
        visible: true,
        actions: [...actions],
        honorAbort,
        startError: null,
        currentSlide: createSlide([["h2", "現在のスライド"]]),
        chara: null,
    };

    const elements = new Map();
    if (configText !== null) elements.set("sphinx-revealjs-mascot-jev-config", new FakeElement("script", { textContent: configText }));

    const document = new EventTarget();
    const addDocumentListener = document.addEventListener.bind(document);
    document.addEventListener = (type, ...rest) => {
        browser.documentListeners.push(type);
        addDocumentListener(type, ...rest);
    };
    document.getElementById = (id) => elements.get(id) ?? null;
    document.readyState = "interactive";

    const window = {
        document,
        location: { search },
        performance: { now: () => clock.now },
        setTimeout: (callback, delay) => clock.setTimeout(callback, delay),
        clearTimeout: (id) => clock.clearTimeout(id),
        fetch: createFetch(browser),
        AbortController,
        URLSearchParams,
        console: {
            debug: (...args) => browser.debugLogs.push(args),
            log: (...args) => browser.consoleProblems.push(["log", ...args]),
            info: (...args) => browser.consoleProblems.push(["info", ...args]),
            warn: (...args) => browser.consoleProblems.push(["warn", ...args]),
            error: (...args) => browser.consoleProblems.push(["error", ...args]),
        },
    };
    window.window = window;
    window.self = window;
    window.parent = inFrame ? {} : window;

    const Recognition = class extends FakeRecognitionBase {
        constructor() {
            super(browser);
        }
    };
    if (recognition === "standard") window.SpeechRecognition = Recognition;
    if (recognition === "webkit") window.webkitSpeechRecognition = Recognition;

    const revealEvents = new EventTarget();
    if (reveal) {
        window.Reveal = {
            on: (type, listener) => {
                browser.revealListeners.push(type);
                revealEvents.addEventListener(type, listener);
            },
            getCurrentSlide: () => browser.currentSlide,
            isReady: () => {
                browser.isReadyCalls += 1;
                return true;
            },
        };
    }

    // mascot.js の showMascot() と同じく、#chara を置いてから RevealMascot を定義する
    const defineMascot = () => {
        browser.chara = new FakeElement("div");
        elements.set("chara", browser.chara);
        window.RevealMascot = Object.freeze({
            getAvailableActions: () => [...browser.actions],
            trigger: (action) => browser.triggers.push({ action, at: clock.now }),
            isVisible: () => browser.visible,
        });
    };
    if (mascot === "immediate") defineMascot();
    // mascot.js は client.js より先に ready のリスナーを登録する（records.md）
    if (mascot === "on-ready") revealEvents.addEventListener("ready", defineMascot);

    vm.createContext(window);
    vm.runInContext(CLIENT_SOURCE, window, { filename: CLIENT_PATH });

    // Object.assign はゲッターを呼んだ値を写すため、記述子ごと定義する
    const helpers = {
        window,
        get recognition() {
            return browser.recognitions[0];
        },
        get listening() {
            return browser.chara?.getAttribute("data-speech-listening") === "true";
        },
        fireReveal(type, detail = {}) {
            revealEvents.dispatchEvent(Object.assign(new Event(type), detail));
        },
        changeSlide(slide = createSlide()) {
            browser.currentSlide = slide;
            browser.fireReveal("slidechanged", { currentSlide: slide });
        },
        press(key, init = {}) {
            const { target = null, ...flags } = init;
            const event = Object.assign(new Event("keydown", { cancelable: true }), { key, altKey: false, ctrlKey: false, metaKey: false, repeat: false, isComposing: false }, flags);
            if (target) Object.defineProperty(event, "target", { value: target });
            document.dispatchEvent(event);
            return event;
        },
        // 発話を1件届け、区切りの無音時間が過ぎるまで進める。送られた判定リクエストを返し、送られなければ undefined を返す
        async speak(text) {
            const count = browser.requests.length;
            browser.recognition.hear(text);
            await clock.advance(1500);
            return browser.requests[count];
        },
    };
    Object.defineProperties(browser, Object.getOwnPropertyDescriptors(helpers));
    return browser;
}

module.exports = { FULL_ACTIONS, REQUIRED4_ACTIONS, FakeElement, createBrowser, createSlide, flush };
