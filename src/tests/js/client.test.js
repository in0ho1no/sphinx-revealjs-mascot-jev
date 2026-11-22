"use strict";
// client.js のテスト（仕様書 4 章・6.2 のクライアントに関する項目）。

const assert = require("node:assert/strict");
const { describe, test } = require("node:test");
const { REQUIRED4_ACTIONS, FakeElement, createBrowser, createSlide, flush } = require("./fake-browser.js");

const UTTERANCE = "ここまで続けてこられたのも皆さんのおかげです";
const COOLDOWNS = {
    tilt: { global: 12000, repeat: 25000 },
    trouble: { global: 15000, repeat: 30000 },
    proud: { global: 15000, repeat: 30000 },
    happy: { global: 15000, repeat: 30000 },
    surprise: { global: 15000, repeat: 35000 },
};

function listeningBrowser(options) {
    const browser = createBrowser(options);
    browser.press("r");
    return browser;
}

// 発話を送り、応答の action で発火させる。発火した時刻を返す
async function fire(browser, action) {
    const request = await browser.speak(UTTERANCE);
    assert.ok(request, "判定リクエストが送られていない");
    request.respond({ action, noul: 0.9, confidence: 0.9 });
    await flush();
    assert.equal(browser.triggers.at(-1)?.action, action);
    return browser.clock.now;
}

// 指定した時刻に確定した結果が届くよう発話し、送られた判定リクエストを返す。
// スライド遷移直後のガードの外では、その時刻に区切りを迎える
async function speakAt(browser, time) {
    const delay = time - browser.clock.now;
    assert.ok(delay >= 0, `${time}ms に届く発話は、もう始められない`);
    await browser.clock.advance(delay);
    return browser.speak(UTTERANCE);
}

function result(transcript, isFinal) {
    return Object.assign([{ transcript }], { isFinal });
}

describe("起動ガード（4.1）", () => {
    for (const [name, options] of [
        ["iframe 内", { inFrame: true }],
        ["?receiver 付きの URL", { search: "?receiver" }],
        ["SpeechRecognition がないブラウザ", { recognition: "none" }],
        ["Reveal がないページ", { reveal: false }],
    ]) {
        test(`${name}では、SpeechRecognition を生成せず、イベントも登録しない`, () => {
            const browser = createBrowser(options);
            browser.press("r");
            assert.equal(browser.recognitions.length, 0);
            assert.deepEqual(browser.revealListeners, []);
            assert.deepEqual(browser.documentListeners, []);
            assert.equal(browser.listening, false);
        });
    }

    test("webkitSpeechRecognition だけのブラウザでも起動する", () => {
        const browser = listeningBrowser({ recognition: "webkit" });
        assert.equal(browser.recognition.startCount, 1);
    });

    test("実行時に RevealMascot があれば、ready を待たずに起動し、Reveal.isReady() を使わない", () => {
        const browser = createBrowser({ mascot: "immediate" });
        assert.equal(browser.recognitions.length, 1);
        assert.deepEqual(browser.revealListeners, ["slidechanged", "fragmentshown"]);
        assert.deepEqual(browser.documentListeners, ["keydown"]);
        assert.equal(browser.isReadyCalls, 0);
    });

    test("実行時に RevealMascot がなければ、ready のリスナーだけを登録し、ready で確かめ直して起動する", () => {
        const browser = createBrowser({ mascot: "on-ready" });
        assert.equal(browser.recognitions.length, 0);
        assert.deepEqual(browser.revealListeners, ["ready"]);
        assert.deepEqual(browser.documentListeners, []);

        browser.fireReveal("ready");
        assert.equal(browser.recognitions.length, 1);
        assert.deepEqual(browser.revealListeners, ["ready", "slidechanged", "fragmentshown"]);
        assert.deepEqual(browser.documentListeners, ["keydown"]);
        assert.equal(browser.isReadyCalls, 0);
    });

    test("ready でも RevealMascot がなければ、起動しない", () => {
        const browser = createBrowser({ mascot: "never" });
        browser.fireReveal("ready");
        assert.equal(browser.recognitions.length, 0);
        assert.deepEqual(browser.documentListeners, []);
    });

    for (const [name, configText] of [
        ["ない", null],
        ["JSON として読めない", "{"],
        ["toggleKey が空", JSON.stringify({ lang: "ja-JP", toggleKey: "" })],
    ]) {
        test(`注入された設定が${name}場合は、起動しない`, () => {
            const browser = createBrowser({ configText });
            assert.equal(browser.recognitions.length, 0);
            assert.deepEqual(browser.revealListeners, []);
            assert.deepEqual(browser.consoleProblems, []);
        });
    }
});

describe("マイクの切り替え（4.2）", () => {
    test("初期状態は OFF で、認識を開始せず、インジケータも付けない", () => {
        const browser = createBrowser();
        assert.equal(browser.recognition.startCount, 0);
        assert.equal(browser.listening, false);
    });

    test("toggleKey で ON にすると、設定した言語で連続・途中結果ありの認識を始め、#chara にインジケータを付ける", () => {
        const browser = createBrowser({ configText: JSON.stringify({ lang: "en-US", toggleKey: "r" }) });
        const event = browser.press("r");
        const recognition = browser.recognition;
        assert.equal(recognition.startCount, 1);
        assert.equal(recognition.lang, "en-US");
        assert.equal(recognition.continuous, true);
        assert.equal(recognition.interimResults, true);
        assert.equal(browser.chara.getAttribute("data-speech-listening"), "true");
        assert.equal(event.defaultPrevented, true);
    });

    test("もう一度押すと OFF になり、認識を中止して、インジケータを外す", () => {
        const browser = listeningBrowser();
        browser.press("r");
        assert.equal(browser.recognition.abortCount, 1);
        assert.equal(browser.chara.getAttribute("data-speech-listening"), null);
    });

    test("大文字（Shift 押下）でも切り替わり、設定した別のキーも使える", () => {
        const browser = createBrowser({ configText: JSON.stringify({ lang: "ja-JP", toggleKey: "v" }) });
        browser.press("r");
        assert.equal(browser.listening, false);
        browser.press("V", { shiftKey: true });
        assert.equal(browser.listening, true);
    });

    for (const [name, init] of [
        ["Alt", { altKey: true }],
        ["Ctrl", { ctrlKey: true }],
        ["Meta", { metaKey: true }],
        ["長押しの繰り返し", { repeat: true }],
        ["IME の変換中", { isComposing: true }],
        ["input へのフォーカス", { target: new FakeElement("input") }],
        ["textarea へのフォーカス", { target: new FakeElement("textarea") }],
        ["select へのフォーカス", { target: new FakeElement("select") }],
        ["contenteditable の要素へのフォーカス", { target: new FakeElement("div", { isContentEditable: true }) }],
    ]) {
        test(`${name}では切り替えない`, () => {
            const browser = createBrowser();
            browser.press("r", init);
            assert.equal(browser.recognition.startCount, 0);
            assert.equal(browser.listening, false);
        });
    }

    test("ON の間に認識が終了したら再開し、OFF の間は再開しない", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.fail("no-speech");
        assert.equal(recognition.startCount, 2);

        browser.press("r");
        await browser.clock.advance(0);
        assert.equal(recognition.running, false);
        await browser.clock.advance(5000);
        assert.equal(recognition.startCount, 2);
    });

    test("中止した認識が終わる前に ON に戻しても例外にならず、終了を待って再開する", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        browser.press("r");
        browser.press("r");
        assert.equal(recognition.startCount, 1);
        assert.equal(browser.listening, true);

        await browser.clock.advance(0);
        assert.equal(recognition.startCount, 2);
        assert.equal(recognition.running, true);
        assert.equal(browser.listening, true);
    });

    test("OFF の前に開始した認識の結果が、ON に戻した後に届いても取り込まない", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        browser.press("r");
        browser.press("r");
        recognition.hear(UTTERANCE);
        await browser.clock.advance(0);
        assert.equal(recognition.startCount, 2);
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 0);

        assert.ok(await browser.speak(UTTERANCE));
    });

    for (const error of ["not-allowed", "service-not-allowed", "language-not-supported"]) {
        test(`${error} のエラーでは OFF に戻り、再開しない`, async () => {
            const browser = listeningBrowser();
            browser.recognition.fail(error);
            assert.equal(browser.listening, false);
            await browser.clock.advance(5000);
            assert.equal(browser.recognition.startCount, 1);
        });
    }

    test("認識を開始できない場合は OFF に戻る", () => {
        const browser = createBrowser();
        browser.startError = new DOMException("blocked", "NotAllowedError");
        browser.press("r");
        assert.equal(browser.listening, false);
    });

    test("それ以外のエラーで終了したら 1000ms 待ってから再開する", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.fail("network");
        await browser.clock.advance(999);
        assert.equal(recognition.startCount, 1);
        await browser.clock.advance(1);
        assert.equal(recognition.startCount, 2);
    });

    test("再開を待つ間に OFF にすると再開せず、ON に戻すとすぐに再開する", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.fail("network");
        browser.press("r");
        await browser.clock.advance(1000);
        assert.equal(recognition.startCount, 1);

        browser.press("r");
        assert.equal(recognition.startCount, 2);
        await browser.clock.advance(1000);
        assert.equal(recognition.startCount, 2);
    });

    test("isVisible() が偽の間の発話は蓄積も送信もしない", async () => {
        const browser = listeningBrowser();
        browser.visible = false;
        assert.equal(await browser.speak(UTTERANCE), undefined);

        browser.recognition.hear("非表示の間に話した内容です");
        browser.visible = true;
        const request = await browser.speak("表示に戻した後の発話です");
        assert.equal(request.body.utterance, "表示に戻した後の発話です");
    });

    test("区切りの時点で isVisible() が偽なら送信しない", async () => {
        const browser = listeningBrowser();
        browser.recognition.hear(UTTERANCE, { isFinal: false });
        browser.visible = false;
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 0);
    });

    test("OFF にすると蓄積中の発話と無音タイマーを消す", async () => {
        const browser = listeningBrowser();
        browser.recognition.hear("OFF の前に話していた内容です", { isFinal: false });
        browser.press("r");
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 0);

        browser.press("r");
        const request = await browser.speak("ON に戻した後の発話です");
        assert.equal(request.body.utterance, "ON に戻した後の発話です");
    });
});

describe("発話の区切りと送信内容（4.3・5.1）", () => {
    test("最後の result から 1500ms 更新がなければ、区切りとして判定を送る", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear("ここまで続けて", { isFinal: false });
        await browser.clock.advance(1000);
        recognition.hear("ここまで続けてこられたのも", { isFinal: false });
        await browser.clock.advance(1499);
        assert.equal(browser.requests.length, 0);
        await browser.clock.advance(1);
        assert.equal(browser.requests.length, 1);

        const request = browser.requests[0];
        assert.equal(request.url, "/__mascot_jev__/decide");
        assert.equal(request.method, "POST");
        // client.js の中で作ったオブジェクトはプロトタイプが別の realm のものになるため、写してから比べる
        assert.deepEqual({ ...request.headers }, { "Content-Type": "application/json" });
        assert.deepEqual(request.body, {
            slide_title: "現在のスライド",
            utterance: "ここまで続けてこられたのも",
            available_actions: ["idle", "jump", "wink", "surprise", "happy", "tilt", "trouble", "proud"],
        });
    });

    test("区切っていない確定した結果と未確定の結果をつなげて送る", async () => {
        const browser = listeningBrowser();
        browser.recognition.hear("それは何か");
        browser.recognition.hear("皆さんのおかげです", { isFinal: false });
        await browser.clock.advance(1500);
        assert.equal(browser.requests[0].body.utterance, "それは何か皆さんのおかげです");
    });

    test("available_actions には getAvailableActions() の戻り値をそのまま入れる", async () => {
        const browser = listeningBrowser({ actions: REQUIRED4_ACTIONS });
        const request = await browser.speak(UTTERANCE);
        assert.deepEqual(request.body.available_actions, ["idle", "jump", "wink", "surprise"]);
    });

    for (const [text, sent] of [
        ["あいうえおかき", false],
        ["あ い う え お か き　く", true],
        [" あ い う え お か き ", false],
        ["あいうえおかきく", true],
    ]) {
        test(`空白を除いて8文字未満なら送らない（${JSON.stringify(text)}）`, async () => {
            const browser = listeningBrowser();
            assert.equal(Boolean(await browser.speak(text)), sent);
        });
    }

    test("utterance は末尾の120文字だけを送る", async () => {
        const browser = listeningBrowser();
        const request = await browser.speak("前".repeat(30) + "😀" + "後".repeat(119));
        assert.equal(request.body.utterance, "😀" + "後".repeat(119));
    });

    test("区切りの後は、次の区切りに前の発話を含めない", async () => {
        const browser = listeningBrowser();
        (await browser.speak("一つ目の区切りの発話です")).respond({ action: null });
        await flush();
        const request = await browser.speak("二つ目の区切りの発話です");
        assert.equal(request.body.utterance, "二つ目の区切りの発話です");
    });

    test("区切りの前に認識が終了・再開しても、区切りまでの発話を引き継ぐ", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear("認識が終わる前の発話と", { isFinal: false });
        recognition.end();
        assert.equal(recognition.startCount, 2);
        const request = await browser.speak("再開した後の発話");
        assert.equal(request.body.utterance, "認識が終わる前の発話と再開した後の発話");
    });

    test("区切りの時点で未確定だった結果が後から同じ位置で伸びたら、伸びた分を次の区切りで送る", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear("はい", { isFinal: false });
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 0);

        recognition.hear("はい、ここまで続けてこられたのも皆さんのおかげです", { isFinal: false });
        await browser.clock.advance(1500);
        assert.equal(browser.requests[0]?.body.utterance, "、ここまで続けてこられたのも皆さんのおかげです");
    });

    test("区切りの時点で未確定だった結果が同じ位置で別の発話に書き換わったら、書き換わった発話を先頭から送る", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear("あいうえおかき", { isFinal: false });
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 0);

        recognition.hear("さしすせそたちつ");
        await browser.clock.advance(1500);
        assert.equal(browser.requests[0]?.body.utterance, "さしすせそたちつ");
    });

    test("区切りで送った未確定の結果が後からそのまま確定しても、もう一度は送らない", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear(UTTERANCE, { isFinal: false });
        await browser.clock.advance(1500);
        browser.requests[0].respond({ action: null });
        await flush();

        recognition.hear(UTTERANCE);
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 1);
    });

    test("区切りで送った未確定の結果が取り消された後に、同じ文言の別の発話が届いたら、もう一度送る", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.deliver([result(UTTERANCE, false)]);
        await browser.clock.advance(1500);
        browser.requests[0].respond({ action: null });
        await flush();

        recognition.deliver([]);
        recognition.deliver([result(UTTERANCE, true)]);
        await browser.clock.advance(1500);
        assert.equal(browser.requests.length, 2);
        assert.equal(browser.requests[1].body.utterance, UTTERANCE);
    });

    test("蓄積から除いた未確定の結果が取り消されたら、同じ位置に届く発話をすべて取り込む", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        browser.visible = false;
        recognition.deliver([result("ノイズのような短い音", false)]);
        browser.visible = true;
        recognition.deliver([]);
        recognition.deliver([result("表示に戻した後の発話です", true)]);
        await browser.clock.advance(1500);
        assert.equal(browser.requests[0]?.body.utterance, "表示に戻した後の発話です");
    });

    test("蓄積を消した時点の未確定の結果が確定して結果の数が減っても、その後の発話を失わない", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        const before = result("非表示の間に確定した発話", true);
        browser.visible = false;
        recognition.deliver([before, result("非表示の間の", false), result("発話", false)]);
        browser.visible = true;
        recognition.deliver([before, result("非表示の間の発話", true)]);
        recognition.deliver([before, result("非表示の間の発話", true), result("表示に戻した後の発話です", true)]);
        await browser.clock.advance(1500);
        assert.equal(browser.requests[0]?.body.utterance, "表示に戻した後の発話です");
    });

    for (const [name, children, expected] of [
        ["最初の h1・h2・h3 の前後の空白を除いた文字列", [["p", "本文"], ["div", "", [["h3", "  入れ子の見出し \n"]]], ["h1", "後の見出し"]], "入れ子の見出し"],
        ["先頭の60文字", [["h2", "あ".repeat(59) + "😀" + "い"]], "あ".repeat(59) + "😀"],
        ["見出しがなければ空文字列", [["p", "見出しのないスライド"], ["h4", "対象外の見出し"]], ""],
    ]) {
        test(`slide_title は、現在のスライドの${name}`, async () => {
            const browser = listeningBrowser();
            browser.currentSlide = createSlide(children);
            const request = await browser.speak(UTTERANCE);
            assert.equal(request.body.slide_title, expected);
        });
    }
});

describe("話し続けた場合の確定した結果での区切り（4.3）", () => {
    test("無音の間がないまま結果が届き続けても、確定した結果が届くたびに、その発話だけを区切って送る", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        const sentences = [
            "最初は全然動かなくて設定の見直しやログの確認を何度も繰り返して苦労しました",
            "それではなぜこのような動きになってしまったのか皆さんはどう思いますか",
            "そこで確認の手順をすべて自動化してボタン一つで終わるようにしたのが一番の工夫です",
            "おかげでリリース前の確認にかかる時間は半日からたったの十分になりました",
        ];
        const finalTimes = [];
        for (const sentence of sentences) {
            const sentenceChars = Array.from(sentence);
            // 500ms ごとに未確定の結果を伸ばし、最後に確定させる。結果の間は 1500ms より短い
            for (let length = 5; length < sentenceChars.length; length += 5) {
                recognition.hear(sentenceChars.slice(0, length).join(""), { isFinal: false });
                await browser.clock.advance(500);
            }
            recognition.hear(sentence);
            finalTimes.push(browser.clock.now);
            await browser.clock.advance(500);
        }

        assert.ok(sentences.join("").length > 120);
        assert.deepEqual(
            browser.requests.map(({ body }) => body.utterance),
            sentences,
        );
        assert.deepEqual(
            browser.requests.map(({ at }) => at),
            finalTimes,
        );
    });

    test("確定した結果の後ろに続く未確定の結果は、次の区切りに残す", () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        const first = result("一つ目の確定した発話です", true);
        recognition.deliver([first, result("二つ目の", false)]);
        assert.equal(browser.requests[0]?.body.utterance, "一つ目の確定した発話です");

        recognition.deliver([first, result("二つ目の確定した発話です", true)]);
        assert.equal(browser.requests[1]?.body.utterance, "二つ目の確定した発話です");
        assert.equal(browser.requests.length, 2);
    });

    test("8文字未満の確定した結果は区切らず、次の確定した結果とまとめて送る", async () => {
        const browser = listeningBrowser();
        browser.recognition.hear("それは何か");
        await browser.clock.advance(1000);
        assert.equal(browser.requests.length, 0);

        browser.recognition.hear("皆さんはどう考えますか");
        assert.equal(browser.requests[0]?.body.utterance, "それは何か皆さんはどう考えますか");
    });

    test("無音で区切って送った未確定の結果が、続きとともに確定したら、続きだけを区切って送る", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear(UTTERANCE, { isFinal: false });
        await browser.clock.advance(1500);
        browser.requests[0].respond({ action: null });
        await flush();

        recognition.hear(UTTERANCE + "本当にありがとうございます");
        assert.equal(browser.requests[1]?.body.utterance, "本当にありがとうございます");
    });

    test("区切りの前に認識が終了・再開したら、引き継いだ発話と、再開後に確定した結果をまとめて送る", () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        recognition.hear("それは何か");
        recognition.end();
        recognition.hear("皆さんはどう考えますか");
        assert.equal(browser.requests[0]?.body.utterance, "それは何か皆さんはどう考えますか");
    });
});

describe("クールタイム（4.4）", () => {
    for (const type of ["slidechanged", "fragmentshown"]) {
        test(`${type} で蓄積中の発話を消し、その後 4000ms の間に無音で区切りを迎えた発話は送らない`, async () => {
            const browser = listeningBrowser();
            browser.recognition.hear("遷移の前に話していた内容です", { isFinal: false });
            browser.fireReveal(type);
            await browser.clock.advance(1500);
            assert.equal(browser.requests.length, 0);

            // ガードの間に届いた確定した結果は、1500ms 後に無音で区切られる
            assert.equal(await speakAt(browser, 3999 - 1500), undefined);
            assert.equal(browser.clock.now, 3999);
            const request = await speakAt(browser, 4000);
            assert.equal(request.body.utterance, UTTERANCE);
        });
    }

    test("ガードの間に確定した発話は区切らず、遷移から 4000ms 後に無音で区切りを迎えれば送る", async () => {
        const browser = listeningBrowser();
        browser.changeSlide();
        await browser.clock.advance(2500);
        browser.recognition.hear(UTTERANCE);
        await browser.clock.advance(1499);
        assert.equal(browser.requests.length, 0);
        await browser.clock.advance(1);
        assert.equal(browser.requests[0]?.body.utterance, UTTERANCE);
    });

    test("ガードの間に確定した結果は、ガードの後に届いた結果で区切り、続く未確定の結果は次の区切りに残す", async () => {
        const browser = listeningBrowser();
        const recognition = browser.recognition;
        browser.changeSlide();
        await browser.clock.advance(3000);
        recognition.hear(UTTERANCE);
        await browser.clock.advance(999);
        recognition.hear("続けて話している", { isFinal: false });
        assert.equal(browser.requests.length, 0);

        await browser.clock.advance(1);
        recognition.hear("続けて話している途中の発話", { isFinal: false });
        assert.equal(browser.requests[0]?.body.utterance, UTTERANCE);
        await browser.clock.advance(1500);
        assert.equal(browser.requests[1]?.body.utterance, "続けて話している途中の発話");
    });

    for (const [action, { global }] of Object.entries(COOLDOWNS)) {
        test(`${action} の発火後 ${global}ms の間は送らず、${global}ms 後から送る`, async () => {
            const browser = listeningBrowser();
            const firedAt = await fire(browser, action);
            assert.equal(await speakAt(browser, firedAt + global - 1), undefined);
            assert.ok(await speakAt(browser, firedAt + global + 1500));

            const other = listeningBrowser();
            const otherFiredAt = await fire(other, action);
            assert.ok(await speakAt(other, otherFiredAt + global));
        });
    }

    test("全体クールタイムの間に確定した発話は送らず、クールタイムの後の区切りにも含めない", async () => {
        const browser = listeningBrowser();
        const firedAt = await fire(browser, "happy");
        await browser.clock.advance(5000);
        browser.recognition.hear("クールタイムの間に話した内容です");
        await browser.clock.advance(firedAt + 15000 - browser.clock.now);
        const request = await browser.speak("クールタイムの後に話した内容です");
        assert.equal(browser.requests.length, 2);
        assert.equal(request.body.utterance, "クールタイムの後に話した内容です");
    });

    for (const [action, { repeat }] of Object.entries(COOLDOWNS)) {
        test(`${action} は発火後 ${repeat}ms の間は再び発火せず、${repeat}ms 後から発火する`, async () => {
            const browser = listeningBrowser();
            const firedAt = await fire(browser, action);
            const suppressed = await speakAt(browser, firedAt + repeat - 1);
            suppressed.respond({ action });
            await flush();
            assert.equal(browser.triggers.length, 1);

            const other = listeningBrowser();
            const otherFiredAt = await fire(other, action);
            (await speakAt(other, otherFiredAt + repeat)).respond({ action });
            await flush();
            assert.equal(other.triggers.length, 2);
        });
    }

    test("同種の抑制で見送った場合は全体クールタイムを始めず、別のアクションは発火する", async () => {
        const browser = listeningBrowser();
        const firedAt = await fire(browser, "happy");
        (await speakAt(browser, firedAt + 15000)).respond({ action: "happy" });
        await flush();
        (await browser.speak(UTTERANCE)).respond({ action: "tilt" });
        await flush();
        assert.deepEqual(
            browser.triggers.map(({ action }) => action),
            ["happy", "tilt"],
        );
    });
});

describe("判定の送信と発火（4.5）", () => {
    test("有効な action が返れば trigger(action) を1回だけ呼ぶ", async () => {
        const browser = listeningBrowser();
        (await browser.speak(UTTERANCE)).respond({ action: "happy", noul: 0.91, confidence: 0.78 });
        await flush();
        assert.deepEqual(browser.triggers, [{ action: "happy", at: 0 }]);
    });

    test("800ms で判定リクエストを中止し、その後に届いた応答は使わない", async () => {
        const browser = listeningBrowser({ honorAbort: false });
        const request = await browser.speak(UTTERANCE);
        await browser.clock.advance(799);
        assert.equal(request.signal.aborted, false);
        await browser.clock.advance(1);
        assert.equal(request.signal.aborted, true);

        request.respond({ action: "happy" });
        await flush();
        assert.deepEqual(browser.triggers, []);
    });

    test("中止のタイマーの実行が遅れても、送信から 800ms を過ぎて届いた応答では発火しない", async () => {
        const browser = listeningBrowser();
        const request = await browser.speak(UTTERANCE);
        // タイマーを実行せずに時刻だけを進め、中止のタイマーの遅れを模す
        browser.clock.now += 801;
        request.respond({ action: "happy" });
        await flush();
        assert.equal(request.signal.aborted, false);
        assert.deepEqual(browser.triggers, []);
    });

    for (const [name, settle] of [
        ["タイムアウト", (request, browser) => browser.clock.advance(800)],
        ["通信エラー", (request) => request.fail()],
        ["200 以外の応答", (request) => request.respond({ action: "happy" }, 500)],
        ["action が null の応答", (request) => request.respond({ action: null })],
        ["本文が null の応答", (request) => request.respond(null)],
        ["音声感情の5種以外の action", (request) => request.respond({ action: "jump" })],
    ]) {
        test(`${name}では発火せず、console.debug 以外に出力しない`, async () => {
            const browser = listeningBrowser();
            await settle(await browser.speak(UTTERANCE), browser);
            await flush();
            assert.deepEqual(browser.triggers, []);
            assert.deepEqual(browser.consoleProblems, []);
        });
    }

    test("送った available_actions にない action では発火しない", async () => {
        const browser = listeningBrowser({ actions: REQUIRED4_ACTIONS });
        (await browser.speak(UTTERANCE)).respond({ action: "happy" });
        await flush();
        assert.deepEqual(browser.triggers, []);
    });

    test("判定の送信後に OFF にするとリクエストを中止し、ON に戻した後に届いた応答も破棄する", async () => {
        const browser = listeningBrowser({ honorAbort: false });
        const request = await browser.speak(UTTERANCE);
        browser.press("r");
        assert.equal(request.signal.aborted, true);

        browser.press("r");
        await browser.clock.advance(0);
        assert.equal(browser.listening, true);
        request.respond({ action: "happy" });
        await flush();
        assert.deepEqual(browser.triggers, []);
    });

    test("判定が重なっている間に OFF にすると、進行中の判定をすべて中止する", async () => {
        const browser = listeningBrowser({ honorAbort: false });
        const first = await browser.speak("一つ目の確定した発話です");
        await browser.clock.advance(200);
        const second = await browser.speak("二つ目の確定した発話です");
        browser.press("r");
        assert.equal(first.signal.aborted, true);
        assert.equal(second.signal.aborted, true);
    });

    test("後の判定が先に終わっても、OFF で先の判定を中止する", async () => {
        const browser = listeningBrowser({ honorAbort: false });
        const first = await browser.speak("一つ目の確定した発話です");
        await browser.clock.advance(200);
        (await browser.speak("二つ目の確定した発話です")).respond({ action: null });
        await flush();
        browser.press("r");
        assert.equal(first.signal.aborted, true);
    });

    test("応答の時点で isVisible() が偽なら発火しない", async () => {
        const browser = listeningBrowser();
        const request = await browser.speak(UTTERANCE);
        browser.visible = false;
        request.respond({ action: "happy" });
        await flush();
        assert.deepEqual(browser.triggers, []);
    });

    test("送信後にスライドが遷移したら、応答が届いても発火しない", async () => {
        const browser = listeningBrowser();
        const request = await browser.speak(UTTERANCE);
        browser.changeSlide();
        request.respond({ action: "happy" });
        await flush();
        assert.deepEqual(browser.triggers, []);
    });
});
