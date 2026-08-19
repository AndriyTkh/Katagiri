---
schema: 2
type: meta
title: Canary probe set
sealed: true
created: 2026-08-19
---

# Canary probe set

**SEALED. Do not read the sentences below for study. Do not copy them anywhere.**

## Purpose

200 Japanese sentences at graded difficulty, sealed on 2026-08-19 at the very start of the
curriculum, before any studying happened. They exist to answer one question that no drill log
can answer: *is unrehearsed listening comprehension actually improving?*

Every other metric in this vault measures material the learner has already met. Review
accuracy, drill streaks and vocabulary counts all rise as a natural consequence of practice,
whether or not comprehension generalises. The canary set is the one probe made of language
that has never been studied, so a rising score on it cannot be explained by familiarity with
the items themselves.

The set is fixed. It is never extended, never edited, never pruned. Editing it would destroy
the comparability that is its entire value.

## Probe protocol

- **Cadence:** once per quarter. Not monthly, not on a whim, not when feeling either
  discouraged or confident. Four data points a year.
- **Sample:** about 20 sentences drawn at random across the five bands, roughly four per
  band. Sentences already used in an earlier probe may be reused; retention of a sentence
  heard once a year ago is not the confound it might seem, but note the overlap.
- **Mode:** listening only. The sentence is spoken (TTS or a human reader); the text is never
  shown, before or after. The reading and English columns exist for the person administering
  the probe, not for the learner.
- **Response:** answered aloud, in English or Ukrainian, immediately. A paraphrase that
  preserves the meaning counts as a pass. Grammatical description of the sentence does not.
- **Unassisted:** one replay maximum. No dictionary, no notes, no pausing to think for a
  minute, no hints from the administrator, no second guesses after feedback.
- **Recorded:** unassisted pass-rate per band, plus the date. Nothing else. Not which
  sentences failed, not why, not what the learner said.
- **Interpretation:** trend line only. A sample of four sentences per band is statistically
  meaningless in isolation - any single probe result is noise, and n=1 statistics on it are
  void. The only legitimate reading is the direction of the line across a year or more. A
  quarter-to-quarter drop of one or two sentences per band means nothing at all.

## Contamination rule

The value of this set decays the moment any of it leaks into studied material. Therefore:

- No sentence and no sentence id from this file may appear anywhere else under
  `docs/katagiri/katagiri/` - not in a lesson, a drill, a sentence set, a vocabulary note, a
  review log, or an inbox capture.
- Any such occurrence is a **validator failure**, not a warning:
  `scripts/validate_canary.py` exits non-zero and names the offending file.
- Failed probe sentences are not looked up, not added to review, not discussed. Grammar
  encountered here is studied from other example sentences, never from these.
- Sealed by tooling, not willpower. The check is meant to run in pre-commit or CI so that
  leakage is caught mechanically rather than remembered.

## Sentence ids

`id = "s-" + sha1(japanese)[:6]`, per the word/sentence ID scheme in `ARCHITECTURE.md`.
The validator recomputes every id from the Japanese column, so a silently edited sentence
fails the check.

## Band b1 - N5 core

Kana-level vocabulary, the copula, basic particles (は・を・の・に・で・から), present-tense polite verbs, simple adjectives. One clause, no subordination.

| id | band | japanese | reading (kana) | english |
| --- | --- | --- | --- | --- |
| s-5a9006 | b1 | 今日はとても暑いです。 | きょうはとてもあついです。 | It is very hot today. |
| s-7d5451 | b1 | これは私のかばんです。 | これはわたしのかばんです。 | This is my bag. |
| s-7a8411 | b1 | 駅はあそこです。 | えきはあそこです。 | The station is over there. |
| s-90c3cb | b1 | 私は毎朝コーヒーを飲みます。 | わたしはまいあさコーヒーをのみます。 | I drink coffee every morning. |
| s-63f91d | b1 | 猫が椅子の上にいます。 | ねこがいすのうえにいます。 | The cat is on the chair. |
| s-10f35c | b1 | この本はとても面白いです。 | このほんはとてもおもしろいです。 | This book is very interesting. |
| s-eefd29 | b1 | 明日は雨です。 | あしたはあめです。 | It will be rainy tomorrow. |
| s-5ef2e9 | b1 | 私は学生ではありません。 | わたしはがくせいではありません。 | I am not a student. |
| s-27cbf6 | b1 | 郵便局は銀行の隣にあります。 | ゆうびんきょくはぎんこうのとなりにあります。 | The post office is next to the bank. |
| s-339dac | b1 | 水を一杯ください。 | みずをいっぱいください。 | A glass of water, please. |
| s-ad5585 | b1 | 今何時ですか。 | いまなんじですか。 | What time is it now? |
| s-8cee10 | b1 | 私の部屋は狭いです。 | わたしのへやはせまいです。 | My room is small. |
| s-b1aefd | b1 | 犬と一緒に公園へ行きます。 | いぬといっしょにこうえんへいきます。 | I go to the park with my dog. |
| s-084268 | b1 | 母は台所にいます。 | はははだいどころにいます。 | My mother is in the kitchen. |
| s-2ec575 | b1 | 兄は先生です。 | あにはせんせいです。 | My older brother is a teacher. |
| s-f151da | b1 | この店は安いです。 | このみせはやすいです。 | This shop is cheap. |
| s-6e54d7 | b1 | 電車で会社に行きます。 | でんしゃでかいしゃにいきます。 | I go to the office by train. |
| s-eacacd | b1 | 私は魚が好きです。 | わたしはさかながすきです。 | I like fish. |
| s-5745d0 | b1 | 今日は金曜日です。 | きょうはきんようびです。 | Today is Friday. |
| s-f20b11 | b1 | 教室に机が五つあります。 | きょうしつにつくえがいつつあります。 | There are five desks in the classroom. |
| s-817c8e | b1 | 空が青いです。 | そらがあおいです。 | The sky is blue. |
| s-93cf9b | b1 | 私は日本語を少し話します。 | わたしはにほんごをすこしはなします。 | I speak a little Japanese. |
| s-15aac9 | b1 | かばんの中に鍵があります。 | かばんのなかにかぎがあります。 | There is a key in the bag. |
| s-373225 | b1 | あの人は誰ですか。 | あのひとはだれですか。 | Who is that person? |
| s-aaf0d4 | b1 | 私は毎日七時に起きます。 | わたしはまいにちしちじにおきます。 | I get up at seven every day. |
| s-590cfa | b1 | 妹は音楽が上手です。 | いもうとはおんがくがじょうずです。 | My younger sister is good at music. |
| s-e49852 | b1 | トイレはどこですか。 | トイレはどこですか。 | Where is the toilet? |
| s-088259 | b1 | この花はとてもきれいです。 | このはなはとてもきれいです。 | This flower is very pretty. |
| s-139bad | b1 | 私はテレビを見ません。 | わたしはテレビをみません。 | I do not watch television. |
| s-8f126f | b1 | 冬は寒いです。 | ふゆはさむいです。 | Winter is cold. |
| s-a7ec5e | b1 | 学校は九時に始まります。 | がっこうはくじにはじまります。 | School starts at nine. |
| s-c75cf5 | b1 | 私はパンと卵を食べます。 | わたしはパンとたまごをたべます。 | I eat bread and eggs. |
| s-83b7e8 | b1 | その車は新しいですか。 | そのくるまはあたらしいですか。 | Is that car new? |
| s-7a978d | b1 | 図書館は静かです。 | としょかんはしずかです。 | The library is quiet. |
| s-b81bec | b1 | 父は新聞を読みます。 | ちちはしんぶんをよみます。 | My father reads the newspaper. |
| s-c25ccd | b1 | 私の家は駅から近いです。 | わたしのいえはえきからちかいです。 | My house is close to the station. |
| s-56317c | b1 | 今日は少し忙しいです。 | きょうはすこしいそがしいです。 | I am a little busy today. |
| s-a7fd3b | b1 | コンビニで牛乳を買います。 | コンビニでぎゅうにゅうをかいます。 | I buy milk at the convenience store. |
| s-0ba12b | b1 | これはいくらですか。 | これはいくらですか。 | How much is this? |
| s-d1a8d4 | b1 | 天気がいいですね。 | てんきがいいですね。 | The weather is nice, isn't it? |

## Band b2 - N5/N4

Past and negative forms, て-form chaining, progressive 〜ています, 〜たい, 〜てください, 〜ましょう, simple reasons with ので, short relative clauses.

| id | band | japanese | reading (kana) | english |
| --- | --- | --- | --- | --- |
| s-c5e8e8 | b2 | 昨日は友達と映画を見ました。 | きのうはともだちとえいがをみました。 | Yesterday I watched a movie with a friend. |
| s-488711 | b2 | 朝ごはんを食べませんでした。 | あさごはんをたべませんでした。 | I did not eat breakfast. |
| s-107448 | b2 | 今、雨が降っています。 | いま、あめがふっています。 | It is raining right now. |
| s-686cde | b2 | 少し待ってください。 | すこしまってください。 | Please wait a moment. |
| s-5de89a | b2 | 週末は何をしましたか。 | しゅうまつはなにをしましたか。 | What did you do on the weekend? |
| s-1ad3c6 | b2 | 私は新しい靴を買いたいです。 | わたしはあたらしいくつをかいたいです。 | I want to buy new shoes. |
| s-68428a | b2 | 電気を消して、部屋を出ました。 | でんきをけして、へやをでました。 | I turned off the light and left the room. |
| s-6f82d5 | b2 | 去年、初めて海外へ行きました。 | きょねん、はじめてかいがいへいきました。 | Last year I went abroad for the first time. |
| s-0b3179 | b2 | その映画はとても長かったです。 | そのえいがはとてもながかったです。 | That movie was very long. |
| s-f8d20d | b2 | 今日は傘を持っていません。 | きょうはかさをもっていません。 | I don't have an umbrella today. |
| s-8606d6 | b2 | 姉は東京で働いています。 | あねはとうきょうではたらいています。 | My older sister works in Tokyo. |
| s-c80d56 | b2 | 熱があるので、今日は休みます。 | ねつがあるので、きょうはやすみます。 | I have a fever, so I will take the day off. |
| s-dd2636 | b2 | 昨日の夜はあまり寝ませんでした。 | きのうのよるはあまりねませんでした。 | I didn't sleep much last night. |
| s-504e54 | b2 | 一緒に昼ごはんを食べましょう。 | いっしょにひるごはんをたべましょう。 | Let's have lunch together. |
| s-d29183 | b2 | ここで写真を撮らないでください。 | ここでしゃしんをとらないでください。 | Please do not take photos here. |
| s-1e1c1b | b2 | 駅まで歩いて十分かかります。 | えきまであるいてじゅっぷんかかります。 | It takes ten minutes on foot to the station. |
| s-ac9610 | b2 | 兄は先週から風邪をひいています。 | あにはせんしゅうからかぜをひいています。 | My older brother has had a cold since last week. |
| s-6c1ed9 | b2 | 私は日本の歌を聞くのが好きです。 | わたしはにほんのうたをきくのがすきです。 | I like listening to Japanese songs. |
| s-df735f | b2 | すみません、もう一度言ってください。 | すみません、もういちどいってください。 | Excuse me, please say that once more. |
| s-a2a4da | b2 | 会議は三時に終わりました。 | かいぎはさんじにおわりました。 | The meeting ended at three. |
| s-fd8f50 | b2 | 昼から天気が悪くなりました。 | ひるからてんきがわるくなりました。 | The weather turned bad from midday. |
| s-fd989a | b2 | 教科書を家に忘れました。 | きょうかしょをいえにわすれました。 | I left my textbook at home. |
| s-8fb26a | b2 | コートを着て、外に出ました。 | コートをきて、そとにでました。 | I put on my coat and went outside. |
| s-648550 | b2 | 今日は何も飲みたくないです。 | きょうはなにものみたくないです。 | I don't want to drink anything today. |
| s-a37ea6 | b2 | その道をまっすぐ行ってください。 | そのみちをまっすぐいってください。 | Please go straight along that road. |
| s-82cfff | b2 | 手を洗ってから、ご飯を食べます。 | てをあらってから、ごはんをたべます。 | I eat after washing my hands. |
| s-db6f00 | b2 | 昨日は掃除をして、洗濯もしました。 | きのうはそうじをして、せんたくもしました。 | Yesterday I cleaned and also did the laundry. |
| s-c88f7a | b2 | 私はまだ宿題をしていません。 | わたしはまだしゅくだいをしていません。 | I have not done my homework yet. |
| s-14a50f | b2 | 春になって、桜が咲きました。 | はるになって、さくらがさきました。 | Spring came and the cherry blossoms bloomed. |
| s-f593e8 | b2 | 電話で母と話していました。 | でんわでははとはなしていました。 | I was talking with my mother on the phone. |
| s-958147 | b2 | 部屋の窓が開いていますよ。 | へやのまどがあいていますよ。 | The window of the room is open. |
| s-2b4606 | b2 | 私は肉をあまり食べません。 | わたしはにくをあまりたべません。 | I don't eat much meat. |
| s-c8677b | b2 | 荷物が重くて、大変でした。 | にもつがおもくて、たいへんでした。 | The luggage was heavy and it was tough. |
| s-4131d5 | b2 | 子供が寝ているので、静かにしてください。 | こどもがねているので、しずかにしてください。 | The child is sleeping, so please be quiet. |
| s-df533b | b2 | 昨日買った本を読んでいます。 | きのうかったほんをよんでいます。 | I am reading the book I bought yesterday. |
| s-20c79a | b2 | この漢字の読み方を教えてください。 | このかんじのよみかたをおしえてください。 | Please tell me how to read this kanji. |
| s-a65d0f | b2 | 夏休みに海で泳ぎたいです。 | なつやすみにうみでおよぎたいです。 | I want to swim in the sea during summer vacation. |
| s-0eee28 | b2 | 財布をなくして、困っています。 | さいふをなくして、こまっています。 | I lost my wallet and I'm in trouble. |
| s-042d54 | b2 | エアコンをつけてもいいですか。 | エアコンをつけてもいいですか。 | May I turn on the air conditioner? |
| s-c5c160 | b2 | 先週の試験は難しくありませんでした。 | せんしゅうのしけんはむずかしくありませんでした。 | Last week's exam was not difficult. |

## Band b3 - N4

Potential forms, giving and receiving (あげる・くれる・もらう・いただく), comparatives and superlatives, conditionals (〜たら・〜ば・〜と・〜なら), obligation, 〜たことがある, 〜ようになる, 〜つもり, hearsay そうです.

| id | band | japanese | reading (kana) | english |
| --- | --- | --- | --- | --- |
| s-49394c | b3 | 私は納豆が食べられません。 | わたしはなっとうがたべられません。 | I can't eat natto. |
| s-e7875c | b3 | 友達に日本語を教えてもらいました。 | ともだちににほんごをおしえてもらいました。 | A friend taught me Japanese. |
| s-a1951c | b3 | 電車よりバスのほうが安いです。 | でんしゃよりバスのほうがやすいです。 | The bus is cheaper than the train. |
| s-5f56a4 | b3 | 雨が降ったら、試合は中止です。 | あめがふったら、しあいはちゅうしです。 | If it rains, the match will be cancelled. |
| s-0e8ace | b3 | 母は私にセーターを買ってくれました。 | はははわたしにセーターをかってくれました。 | My mother bought me a sweater. |
| s-a175d0 | b3 | 富士山に登ったことがありますか。 | ふじさんにのぼったことがありますか。 | Have you ever climbed Mount Fuji? |
| s-9e7b36 | b3 | 明日までにこの書類を出さなければなりません。 | あしたまでにこのしょるいをださなければなりません。 | I have to submit this document by tomorrow. |
| s-784e57 | b3 | 最近、少し漢字が読めるようになりました。 | さいきん、すこしかんじがよめるようになりました。 | Recently I have become able to read a little kanji. |
| s-165de0 | b3 | 音楽を聞きながら仕事をします。 | おんがくをききながらしごとをします。 | I work while listening to music. |
| s-8114c3 | b3 | 弟に古い自転車をあげました。 | おとうとにふるいじてんしゃをあげました。 | I gave my younger brother my old bicycle. |
| s-2101d0 | b3 | この店の中で一番おいしいのはラーメンです。 | このみせのなかでいちばんおいしいのはラーメンです。 | The tastiest thing in this shop is the ramen. |
| s-cafcb5 | b3 | 時間があれば、美術館に行きたいです。 | じかんがあれば、びじゅつかんにいきたいです。 | If I have time, I want to go to the art museum. |
| s-9194ec | b3 | 彼が来るかどうか分かりません。 | かれがくるかどうかわかりません。 | I don't know whether he is coming. |
| s-16947f | b3 | 冬は日が短くなるので、早く暗くなります。 | ふゆはひがみじかくなるので、はやくくらくなります。 | In winter the days get shorter, so it gets dark early. |
| s-32818e | b3 | 週末は家で休むつもりです。 | しゅうまつはいえでやすむつもりです。 | I intend to rest at home this weekend. |
| s-eacbcf | b3 | 部長に会議の場所を聞いてみます。 | ぶちょうにかいぎのばしょをきいてみます。 | I'll try asking the manager where the meeting is. |
| s-0a678e | b3 | この辞書を貸してくれませんか。 | このじしょをかしてくれませんか。 | Could you lend me this dictionary? |
| s-2528ef | b3 | たくさん練習すれば、上手になります。 | たくさんれんしゅうすれば、じょうずになります。 | If you practise a lot, you will get good at it. |
| s-0a40d1 | b3 | 私は運転ができないので、いつも電車で通っています。 | わたしはうんてんができないので、いつもでんしゃでかよっています。 | I can't drive, so I always commute by train. |
| s-d530d0 | b3 | 隣の部屋がうるさくて、集中できません。 | となりのへやがうるさくて、しゅうちゅうできません。 | The next room is noisy and I can't concentrate. |
| s-19d2cb | b3 | 先生に作文を直していただきました。 | せんせいにさくぶんをなおしていただきました。 | The teacher kindly corrected my essay. |
| s-c18fa8 | b3 | この道を右に曲がると、公園が見えます。 | このみちをみぎにまがると、こうえんがみえます。 | If you turn right on this road, you'll see a park. |
| s-035fc8 | b3 | 妹は私より三歳年下です。 | いもうとはわたしよりさんさいとしたです。 | My younger sister is three years younger than me. |
| s-010a64 | b3 | 熱が下がらなかったら、病院に行きます。 | ねつがさがらなかったら、びょういんにいきます。 | If the fever doesn't go down, I'll go to the hospital. |
| s-08a298 | b3 | 日本語で電話をかけるのはまだ難しいです。 | にほんごででんわをかけるのはまだむずかしいです。 | Making phone calls in Japanese is still hard for me. |
| s-73ba90 | b3 | 手伝ってあげましょうか。 | てつだってあげましょうか。 | Shall I help you? |
| s-d92f60 | b3 | 甘いものはあまり好きではありませんが、ケーキは食べられます。 | あまいものはあまりすきではありませんが、ケーキはたべられます。 | I don't much like sweet things, but I can eat cake. |
| s-054219 | b3 | 天気予報によると、明日は晴れるそうです。 | てんきよほうによると、あしたははれるそうです。 | According to the forecast, it will be sunny tomorrow. |
| s-cff840 | b3 | 部屋を出るとき、鍵を閉めてください。 | へやをでるとき、かぎをしめてください。 | When you leave the room, please lock it. |
| s-ed776e | b3 | 会議が長引きそうなので、先に帰ります。 | かいぎがながびきそうなので、さきにかえります。 | The meeting looks like it will drag on, so I'll leave first. |
| s-37252e | b3 | 私は英語よりフランス語のほうが難しいと思います。 | わたしはえいごよりフランスごのほうがむずかしいとおもいます。 | I think French is harder than English. |
| s-bcfbdb | b3 | 明日雪が降るかもしれません。 | あしたゆきがふるかもしれません。 | It might snow tomorrow. |
| s-7fad42 | b3 | 兄は仕事のために大阪に引っ越しました。 | あにはしごとのためにおおさかにひっこしました。 | My older brother moved to Osaka for work. |
| s-072ff8 | b3 | この機械の使い方を説明してくれますか。 | このきかいのつかいかたをせつめいしてくれますか。 | Could you explain how to use this machine? |
| s-27b657 | b3 | パソコンが壊れて、修理に出しました。 | パソコンがこわれて、しゅうりにだしました。 | My computer broke, so I sent it for repair. |
| s-4cf06f | b3 | 疲れているなら、少し休んだほうがいいですよ。 | つかれているなら、すこしやすんだほうがいいですよ。 | If you're tired, you had better rest a little. |
| s-74d850 | b3 | 駅に着いたら、電話してください。 | えきについたら、でんわしてください。 | When you arrive at the station, please call me. |
| s-2af450 | b3 | 私は毎日三十分ぐらい歩くことにしています。 | わたしはまいにちさんじゅっぷんぐらいあるくことにしています。 | I make a point of walking about thirty minutes every day. |
| s-1b6bd5 | b3 | 財布を落としたのに、誰も気づきませんでした。 | さいふをおとしたのに、だれもきづきませんでした。 | Even though I dropped my wallet, nobody noticed. |
| s-03e98a | b3 | 隣の家からカレーの匂いがしてきます。 | となりのいえからカレーのにおいがしてきます。 | The smell of curry is drifting over from the house next door. |

## Band b4 - N3

Passive and causative (including causative-passive), multi-clause sentences, cause and concession (せいで・おかげで・ため・のに), 〜ば〜ほど, 〜にとって, 〜にくい・〜やすい, 〜うちに, 〜ばかり, everyday abstract topics.

| id | band | japanese | reading (kana) | english |
| --- | --- | --- | --- | --- |
| s-9344f4 | b4 | 会議で新しい計画が発表されました。 | かいぎであたらしいけいかくがはっぴょうされました。 | A new plan was announced at the meeting. |
| s-cb47c9 | b4 | 子供のころ、母によく野菜を食べさせられました。 | こどものころ、ははによくやさいをたべさせられました。 | As a child, I was often made to eat vegetables by my mother. |
| s-e2d3d5 | b4 | 部長は私に報告書を書かせました。 | ぶちょうはわたしにほうこくしょをかかせました。 | The manager had me write the report. |
| s-538ed2 | b4 | その事故のせいで、電車が二時間も遅れました。 | そのじこのせいで、でんしゃがにじかんもおくれました。 | Because of that accident, the train was two whole hours late. |
| s-019a5f | b4 | 友達のおかげで、引っ越しが早く終わりました。 | ともだちのおかげで、ひっこしがはやくおわりました。 | Thanks to my friends, the move finished quickly. |
| s-e7793d | b4 | 私にとって、家族と過ごす時間が一番大切です。 | わたしにとって、かぞくとすごすじかんがいちばんたいせつです。 | For me, time spent with family matters most. |
| s-ac2e8f | b4 | 練習すればするほど、発音がよくなります。 | れんしゅうすればするほど、はつおんがよくなります。 | The more you practise, the better your pronunciation gets. |
| s-8dee47 | b4 | 使われなくなった建物が町の中心に残っています。 | つかわれなくなったたてものがまちのちゅうしんにのこっています。 | A building that fell out of use still stands in the town centre. |
| s-2afdcb | b4 | 彼は約束を忘れたわけではなく、ただ連絡できなかったようです。 | かれはやくそくをわすれたわけではなく、ただれんらくできなかったようです。 | It's not that he forgot the promise; he just seems to have been unable to get in touch. |
| s-17f053 | b4 | 隣の人に足を踏まれて、少し痛かったです。 | となりのひとにあしをふまれて、すこしいたかったです。 | The person next to me stepped on my foot and it hurt a little. |
| s-85fefd | b4 | 遅れないように、家を早く出ました。 | おくれないように、いえをはやくでました。 | I left home early so as not to be late. |
| s-e9acc2 | b4 | 上司に残業を頼まれましたが、断りました。 | じょうしにざんぎょうをたのまれましたが、ことわりました。 | My boss asked me to work overtime, but I declined. |
| s-5cf1a1 | b4 | この習慣は江戸時代から続いていると言われています。 | このしゅうかんはえどじだいからつづいているといわれています。 | This custom is said to have continued since the Edo period. |
| s-49b01f | b4 | 説明が複雑すぎて、半分しか理解できませんでした。 | せつめいがふくざつすぎて、はんぶんしかりかいできませんでした。 | The explanation was too complicated; I understood only half of it. |
| s-359465 | b4 | 天気が変わりやすいので、上着を持っていったほうがいいです。 | てんきがかわりやすいので、うわぎをもっていったほうがいいです。 | The weather changes easily, so you'd better take a jacket. |
| s-354f5c | b4 | 若いころは、よく夜遅くまで本を読んだものです。 | わかいころは、よくよるおそくまでほんをよんだものです。 | When I was young, I used to read late into the night. |
| s-6eea94 | b4 | 締め切りが近づくにつれて、みんな静かになりました。 | しめきりがちかづくにつれて、みんなしずかになりました。 | As the deadline drew closer, everyone grew quiet. |
| s-303f11 | b4 | その質問には答えにくいので、後で考えます。 | そのしつもんにはこたえにくいので、あとでかんがえます。 | That question is hard to answer, so I'll think about it later. |
| s-6ae5b7 | b4 | 部屋を片付けているうちに、昔の写真が見つかりました。 | へやをかたづけているうちに、むかしのしゃしんがみつかりました。 | While tidying the room, I came across old photographs. |
| s-0169b9 | b4 | 電車の中で子供に泣かれて、母親は困っていました。 | でんしゃのなかでこどもになかれて、ははおやはこまっていました。 | Her child cried on the train and the mother was at a loss. |
| s-5efaea | b4 | 私の意見では、この案にはもう少し検討が必要です。 | わたしのいけんでは、このあんにはもうすこしけんとうがひつようです。 | In my opinion, this proposal needs a bit more examination. |
| s-ec5860 | b4 | 熱があるのに無理をしたら、かえって悪くなりました。 | ねつがあるのにむりをしたら、かえってわるくなりました。 | I pushed myself despite the fever, and it only got worse. |
| s-db5562 | b4 | 大人になると、時間が早く過ぎるように感じます。 | おとなになると、じかんがはやくすぎるようにかんじます。 | Once you become an adult, time feels like it passes faster. |
| s-defec3 | b4 | 会社の方針が変わったため、計画を見直すことになりました。 | かいしゃのほうしんがかわったため、けいかくをみなおすことになりました。 | Because company policy changed, we ended up revising the plan. |
| s-9f4df8 | b4 | 誰かに名前を呼ばれたような気がしました。 | だれかになまえをよばれたようなきがしました。 | I had the feeling someone called my name. |
| s-880fd5 | b4 | 子供に自分で考えさせることが大事だと思います。 | こどもにじぶんでかんがえさせることがだいじだとおもいます。 | I think it is important to let children think for themselves. |
| s-92aa5c | b4 | 静かな場所で働けるなら、給料が少し低くても構いません。 | しずかなばしょではたらけるなら、きゅうりょうがすこしひくくてもかまいません。 | If I can work somewhere quiet, I don't mind a slightly lower salary. |
| s-c0913b | b4 | その本は先月出版されたばかりです。 | そのほんはせんげつしゅっぱんされたばかりです。 | That book was published only last month. |
| s-1b4e4c | b4 | 道に迷ってしまい、知らない人に道を尋ねました。 | みちにまよってしまい、しらないひとにみちをたずねました。 | I got lost and asked a stranger for directions. |
| s-fccb92 | b4 | 最近、朝の散歩が習慣になってきました。 | さいきん、あさのさんぽがしゅうかんになってきました。 | Lately the morning walk has been turning into a habit. |
| s-fec4a8 | b4 | 期待していたほど面白い映画ではありませんでした。 | きたいしていたほどおもしろいえいがではありませんでした。 | The film was not as interesting as I had hoped. |
| s-e4d6a3 | b4 | 雨に降られて、服がすっかり濡れてしまいました。 | あめにふられて、ふくがすっかりぬれてしまいました。 | I got caught in the rain and my clothes were completely soaked. |
| s-c42a75 | b4 | 私は人前で話すことに慣れていません。 | わたしはひとまえではなすことになれていません。 | I am not used to speaking in front of people. |
| s-fb4053 | b4 | 予約をしておかないと、席が取れないかもしれません。 | よやくをしておかないと、せきがとれないかもしれません。 | If we don't book in advance, we may not get seats. |
| s-78449a | b4 | 彼女の話し方から、かなり緊張しているのが分かりました。 | かのじょのはなしかたから、かなりきんちょうしているのがわかりました。 | From the way she spoke, I could tell she was quite nervous. |
| s-50ec5f | b4 | この仕事は思っていたより責任が重いと感じています。 | このしごとはおもっていたよりせきにんがおもいとかんじています。 | I feel this job carries heavier responsibility than I expected. |
| s-6aef10 | b4 | 部下に無理な仕事をさせるわけにはいきません。 | ぶかにむりなしごとをさせるわけにはいきません。 | I cannot make my subordinates do unreasonable work. |
| s-1ecfac | b4 | 一度決めたことは、簡単には変えられません。 | いちどきめたことは、かんたんにはかえられません。 | Once something is decided, it cannot easily be changed. |
| s-0017a4 | b4 | 天気が悪かったせいか、客はほとんど来ませんでした。 | てんきがわるかったせいか、きゃくはほとんどきませんでした。 | Perhaps because of the bad weather, hardly any customers came. |
| s-0792fb | b4 | 何度も注意されたのに、同じ間違いを繰り返してしまいました。 | なんどもちゅういされたのに、おなじまちがいをくりかえしてしまいました。 | Although I was warned many times, I repeated the same mistake. |

## Band b5 - N2 and above

Nuanced grammar (〜ざるを得ない・〜にもかかわらず・〜どころか・〜ものの・〜つつある・〜次第・〜あげく・〜末に・〜限り), a deliberate spread of register from keigo to blunt casual speech, and idiomatic expressions.

| id | band | japanese | reading (kana) | english |
| --- | --- | --- | --- | --- |
| s-35c6f0 | b5 | 事情を考えれば、彼の判断を認めざるを得ないだろう。 | じじょうをかんがえれば、かれのはんだんをみとめざるをえないだろう。 | Given the circumstances, one has little choice but to accept his judgement. |
| s-d0ad70 | b5 | 悪天候にもかかわらず、開会式は予定通り行われた。 | あくてんこうにもかかわらず、かいかいしきはよていどおりおこなわれた。 | Despite the bad weather, the opening ceremony was held as scheduled. |
| s-dc96d3 | b5 | 忙しいのは分かるけど、連絡くらいしてくれてもいいんじゃない。 | いそがしいのはわかるけど、れんらくくらいしてくれてもいいんじゃない。 | I get that you're busy, but you could at least drop me a line. |
| s-56124c | b5 | この制度は少しずつ見直されつつある。 | このせいどはすこしずつみなおされつつある。 | This system is gradually being reconsidered. |
| s-2bebc1 | b5 | 反対したものの、最終的には多数の意見に従った。 | はんたいしたものの、さいしゅうてきにはたすうのいけんにしたがった。 | Although I objected, in the end I went along with the majority. |
| s-6f29c3 | b5 | 給料が上がったからといって、生活が楽になるわけではない。 | きゅうりょうがあがったからといって、せいかつがらくになるわけではない。 | Just because your pay goes up doesn't mean life gets easier. |
| s-d0eb1a | b5 | 彼の説明はどうも要領を得なかった。 | かれのせつめいはどうもようりょうをえなかった。 | His explanation somehow never got to the point. |
| s-32c566 | b5 | あんなに練習したのに、本番で緊張しちゃって全然だめだった。 | あんなにれんしゅうしたのに、ほんばんできんちょうしちゃってぜんぜんだめだった。 | After all that practice, I froze up on the day and it was a total flop. |
| s-c994ff | b5 | 結論が出次第、関係者全員に通知いたします。 | けつろんがでしだい、かんけいしゃぜんいんにつうちいたします。 | As soon as a conclusion is reached, we will notify all parties concerned. |
| s-a2ed70 | b5 | 引き受けたはいいが、思った以上に手間がかかっている。 | ひきうけたはいいが、おもったいじょうにてまがかかっている。 | I took it on readily enough, but it's proving more trouble than I thought. |
| s-eae44d | b5 | 会議では誰も本音を言おうとしなかった。 | かいぎではだれもほんねをいおうとしなかった。 | At the meeting nobody would say what they really thought. |
| s-1ee093 | b5 | その件については、後日改めてご連絡させていただきます。 | そのけんについては、ごじつあらためてごれんらくさせていただきます。 | Regarding that matter, allow me to contact you again at a later date. |
| s-e84125 | b5 | 彼は仕事に手を抜くような人ではない。 | かれはしごとにてをぬくようなひとではない。 | He is not the sort of person to cut corners at work. |
| s-79ffcf | b5 | 今さら文句を言ったところで、状況は変わらない。 | いまさらもんくをいったところで、じょうきょうはかわらない。 | Complaining at this stage won't change the situation. |
| s-52999b | b5 | 締め切りが迫っているので、のんびりしている場合ではない。 | しめきりがせまっているので、のんびりしているばあいではない。 | The deadline is looming; this is no time to be taking it easy. |
| s-772484 | b5 | 内容はともかく、まずは期限を守ることが大切だ。 | ないようはともかく、まずはきげんをまもることがたいせつだ。 | Content aside, the first priority is meeting the deadline. |
| s-e4fc9c | b5 | 正直言うと、あの提案には気が進まない。 | しょうじきいうと、あのていあんにはきがすすまない。 | To be honest, I'm not keen on that proposal. |
| s-663c90 | b5 | 雨のせいか、なんとなく気分が沈んでいる。 | あめのせいか、なんとなくきぶんがしずんでいる。 | Maybe it's the rain, but my mood is somehow low. |
| s-0de2cd | b5 | 一年も経つと、あの騒ぎもすっかり忘れられてしまった。 | いちねんもたつと、あのさわぎもすっかりわすれられてしまった。 | After a whole year, that fuss has been completely forgotten. |
| s-25ecb7 | b5 | 経験がないからこそ、思い切った発想ができたのだと思う。 | けいけんがないからこそ、おもいきったはっそうができたのだとおもう。 | It is precisely because of a lack of experience that such a bold idea was possible. |
| s-8219d0 | b5 | 彼女は疲れを見せないどころか、最後まで笑顔だった。 | かのじょはつかれをみせないどころか、さいごまでえがおだった。 | Far from showing fatigue, she was smiling to the very end. |
| s-51892f | b5 | 母に言わせれば、私の部屋は物置き同然らしい。 | ははにいわせれば、わたしのへやはものおきどうぜんらしい。 | If you ask my mother, my room is little better than a storeroom. |
| s-dec555 | b5 | こんな時間に電話してくるなんて、よほど急ぎの用だろう。 | こんなじかんにでんわしてくるなんて、よほどいそぎのようだろう。 | Calling at this hour, it must be quite urgent. |
| s-c48bbf | b5 | 話を聞くうちに、かえって疑問が増えてしまった。 | はなしをきくうちに、かえってぎもんがふえてしまった。 | As I listened, my questions only multiplied. |
| s-68b113 | b5 | 天気が回復しない限り、出発は延期するしかない。 | てんきがかいふくしないかぎり、しゅっぱつはえんきするしかない。 | Unless the weather improves, there's no choice but to postpone departure. |
| s-7c1d02 | b5 | あの店、混んでるって聞いてたけど、意外とすいてたよ。 | あのみせ、こんでるってきいてたけど、いがいとすいてたよ。 | I'd heard that place was packed, but it was surprisingly empty. |
| s-801307 | b5 | 上司の一言で、社内の雰囲気が一気に変わった。 | じょうしのひとことで、しゃないのふんいきがいっきにかわった。 | One word from the boss changed the mood in the office at a stroke. |
| s-ce018d | b5 | 若いうちに苦労しておいて損はない。 | わかいうちにくろうしておいてそんはない。 | There's nothing to lose by having a hard time while you're young. |
| s-2a9bca | b5 | 予算の都合上、今年度の実施は見送ることになった。 | よさんのつごうじょう、こんねんどのじっしはみおくることになった。 | For budgetary reasons, implementation this fiscal year has been shelved. |
| s-44f30b | b5 | 遠慮せずに、思っていることを言ってくれればいい。 | えんりょせずに、おもっていることをいってくれればいい。 | Don't hold back; just tell me what you're thinking. |
| s-8eee68 | b5 | 気づいたときには、もう手遅れだった。 | きづいたときには、もうておくれだった。 | By the time I noticed, it was already too late. |
| s-f028d6 | b5 | 何度も読み返したあげく、結局最初の案に戻した。 | なんどもよみかえしたあげく、けっきょくさいしょのあんにもどした。 | After rereading it over and over, I went back to the first draft after all. |
| s-992c23 | b5 | この辺りは夜になると人通りがぱったり途絶える。 | このあたりはよるになるとひとどおりがぱったりとだえる。 | Around here the foot traffic dies away completely at night. |
| s-e2a4a4 | b5 | 陰で支えてくれた人の努力も評価されるべきだ。 | かげでささえてくれたひとのどりょくもひょうかされるべきだ。 | The efforts of those who supported us behind the scenes deserve recognition too. |
| s-1e2063 | b5 | うまくいくかどうかは、やってみないことには分からない。 | うまくいくかどうかは、やってみないことにはわからない。 | Whether it works out is something you can't know without trying. |
| s-646938 | b5 | さんざん迷った末に、留学を決めた。 | さんざんまよったすえに、りゅうがくをきめた。 | After agonising over it, I decided to study abroad. |
| s-18c4d4 | b5 | 相手の立場に立って考えないと、話は前に進まない。 | あいてのたちばにたってかんがえないと、はなしはまえにすすまない。 | Unless you consider the other side's position, the discussion won't move forward. |
| s-61223f | b5 | あいつはいつも口ばっかりで、実際には何もやらない。 | あいつはいつもくちばっかりで、じっさいにはなにもやらない。 | That guy is all talk and never actually does anything. |
| s-96d70f | b5 | お忙しいところ恐れ入りますが、少しお時間いただけますか。 | おいそがしいところおそれいりますが、すこしおじかんいただけますか。 | I'm sorry to trouble you when you're busy, but could I have a moment? |
| s-61f053 | b5 | 慣れてくると、細かいところまで気が回るようになる。 | なれてくると、こまかいところまできがまわるようになる。 | Once you get used to it, you start noticing even the small details. |
