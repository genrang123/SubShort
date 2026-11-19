# Plan: các gap còn lại của SubShort so với DichVideo

Ngày đối chiếu ban đầu: 2026-11-16; cập nhật bằng chứng: 2026-11-17. **Phase 1–6 đã có triển khai và kiểm chứng theo phạm vi ghi ở từng phase; Phase 7–12 còn gate nghiệm thu, không đồng nghĩa chưa có source. Phase 13 ngoài scope bản cá nhân.**

## 1. Baseline và phạm vi

- Reference DichVideo: [context đã phân tích](../../DichVideo/analysis/DICHVIDEO_CONTEXT.md), executable SHA-256 `a477b093fb3f4730bf93fc16c7232c98988e0bffb6a6f494aa63cea5ec82ac1d`.
- Baseline SubShort: source hiện nằm trong `src/subshort/`, cấu hình, test và packaging hiện tại; các đường dẫn source `subshort/` trong ghi chép lịch sử bên dưới tương ứng `src/subshort/` sau khi chuẩn hóa layout.
- Đối chiếu ban đầu dựa trên source và bằng chứng `[STATIC]`/`[ARTIFACT]` phía DichVideo. Các test, model smoke, build và UI quan sát sau đó được ghi riêng tại từng phase; chưa có kết luận chất lượng tương đương toàn bộ.
- Mục tiêu đề xuất: parity pipeline và trải nghiệm sử dụng. Cơ chế trial/lượt/định danh máy thương mại được ghi riêng, không mặc định đưa vào bản dùng cá nhân.
- Mỗi checkbox chỉ được đánh dấu khi có bằng chứng nghiệm thu; có field, dependency hoặc adapter chưa đủ để coi là hoàn tất.

### Phạm vi đã chốt — 2026-11-17

- Người dùng xác nhận phạm vi G01–G21; giữ yêu cầu model/backend cụ thể tại từng gap. Không đánh dấu hoàn tất bằng adapter, mock hoặc metadata thay cho bằng chứng nghiệm thu.
- Phân biệt đã triển khai, đã kiểm chứng tích hợp và đã nghiệm thu chất lượng theo [glossary](GLOSSARY.md). Nhánh thiếu hardware/credential/dịch vụ vẫn để mở.
- G22 và Phase 13 ngoài phạm vi bản dùng cá nhân, là khác biệt có chủ đích; không phải tính năng đã triển khai.
- Cho phép kiểm chứng local và tải model/tool từ nguồn chính thức sau kiểm tra giấy phép. Không tự dùng credential tìm thấy, gọi API tính phí, gửi media/text ra ngoài hoặc upload Drive khi chưa có quyền cụ thể cho dữ liệu/dịch vụ đó.
- Fallback chỉ GPU → CPU trong cùng engine/model, ghi chẩn đoán; không tự đổi model/provider.
- Tiếp tục/retry giữ snapshot của job; chạy cấu hình hiện tại tạo job mới. Reuse chỉ khi fingerprint source/config/content/model liên quan còn khớp.
- Upload Drive mặc định tắt, trạng thái/retry độc lập render; lỗi hoặc hủy upload không xóa video local.
- SubShort độc lập thư mục DichVideo: package/model từ upstream chính thức, model nặng ngoài executable; hiển thị nguồn/dung lượng/giấy phép trước tải, không phân phối lại worker/giọng DichVideo khi chưa rõ quyền.
- Baseline phát hành Windows 11 x64 chạy CPU đầy đủ; CUDA/NVENC tùy chọn, chỉ nghiệm thu khi có bằng chứng chạy trên hardware tương ứng. Chưa chạy không đồng nghĩa đã pass.
- Sample nghiệm thu công khai có quyền sử dụng: Trung → Việt và thêm tiếng Anh. Preset phải rõ quyền; custom voice cần sự cho phép của chủ giọng. Không tự dùng media/giọng có sẵn trong workspace.
- Kiểm chứng kỹ thuật/nội dung đo được do agent thực hiện; nghiệm thu chất lượng nghe/nhìn cần người dùng đánh giá, giữ mở tới khi có xác nhận.
- Mỗi dịch vụ ngoài cần quyền rõ theo provider/model và loại dữ liệu gửi (text dịch/TTS, frame vision, file upload); Edge TTS cũng là network. Không tự đổi endpoint/provider; hook/vision/upload mặc định tắt. Lưu key không tự cấp quyền gọi API tính phí.
- Người dùng xác nhận hiểu chung và cho phép bắt đầu triển khai Phase 6–12; các quyền cloud và nghiệm thu chất lượng vẫn cần xác nhận riêng như trên.
- Xác nhận tiếp ngày 2026-11-17: người dùng chọn **chỉ tiếp tục local**, giữ gate cloud/OmniVoice mở. Không dùng kết quả local để tự đánh dấu nghiệm thu OpenAI/Gemini/Edge/vision/Drive hoặc chấp thuận giấy phép OmniVoice.
- Chốt tiếp sau nghiệm thu `example1`: giọng Việt và phụ đề khớp nhau nhưng lệch lượt thoại nguồn. **Phase 5B bên dưới là ưu tiên P0 kế tiếp**, thay contract gộp để đọc và đẩy câu sau của P4.3/P5.2–P5.4 trong nhánh có TTS. Các checkbox lịch sử ghi triển khai cũ, không chứng minh đã đạt contract mới. Đây là thiết kế SubShort được người dùng chốt, không phải hành vi DichVideo đã xác minh.

## 2. Phần đã có — giữ và hoàn thiện, không viết lại từ đầu

| Nền tảng SubShort | Bằng chứng hiện tại | Giới hạn |
|---|---|---|
| Desktop Tk, nhập nhiều file local, cập nhật trạng thái qua `after` | `subshort/app.py`: `App`, `add`, `notify` | Chưa có bề mặt cấu hình đầy đủ, progress chi tiết, cancel |
| Lưu job JSON và nạp lại | `subshort/queue.py`: `JobQueue` | Chưa atomic/recovery; khác resume stage |
| Trích audio mono 16 kHz từ hình gốc | `subshort/media.py`: `extract_audio` | Chưa kiểm tra media/no-audio đầy đủ |
| Whisper small local | `subshort/providers.py`: `LocalWhisper` | Chưa chọn model/device, fallback hoặc reuse |
| Gọi QwenWorker bằng request/result JSON | `QwenRuntimeASR` | Chia cứng 60 giây; chưa VAD/session; chưa chứng minh route FireRed đúng |
| Adapter FireRed/VieNeu command ngoài | `FireRedASR`, `VieNeuTTS` | Người dùng phải cung cấp wrapper; chưa là tích hợp model hoàn chỉnh |
| OpenAI translation, Edge TTS | `OpenAITranslator`, `EdgeTTS` | Dịch theo dòng thiếu validation; Edge không thay thế parity VieNeu/OmniVoice |
| SRT, gộp cue, JSON timeline, delay/mix audio, burn subtitle | `merge_segments`, `build_timeline`, `write_srt`; `MediaAdapter` | Có implementation nhưng semantics khác DichVideo |
| Render hình gốc một bước cuối | `Pipeline.run` → `MediaAdapter.render` | Chưa timeline/alignment/layout/encoder tương đương |
| Bốn test nhỏ | `tests/test_core.py` | Chỉ SRT, gộp theo số segment, ghép metadata timeline, queue roundtrip |

## 3. Các sai lệch cần xử lý trước khi thêm tính năng

Đây là rủi ro suy ra trực tiếp từ source, chưa phải lỗi tái hiện bằng runtime.

1. `Pipeline.run` truyền `settings.target_language` vào ASR nguồn. Ngôn ngữ nguồn đang bị trộn với ngôn ngữ đích; job lại dùng `job.target_language` ở bước dịch.
2. `slow_speed` được khai báo nhưng không được pipeline sử dụng. `final_speed` chỉ áp lên hình trong `render`; audio và SRT không chuyển timebase tương ứng. `-shortest` có thể cắt nội dung khi hình/audio khác thời lượng.
3. `timeline_audio` delay theo timestamp nguồn rồi `amix`; không đo thời lượng clip hay đẩy câu sau. `build_timeline` chỉ chép bounds nguồn, không ghi vị trí giọng thực tế.
4. `OpenAITranslator.translate` ánh xạ bằng số dòng; thiếu dòng tự thay bằng text nguồn, dư dòng không được kiểm tra. Job có thể báo xong với nội dung chưa dịch hoặc lệch câu.
5. `App.run` tạo thread cho mọi job pending, không batch tuần tự; có thể bấm chạy tiếp khi job chưa kịp chuyển running. Model nặng được tải riêng theo job.
6. `App.api` chỉ hỏi key rồi bỏ giá trị. `Settings.save` ghi key OpenAI/Gemini dạng rõ vào JSON.
7. `Job.mode`, `separate_background`, `qwen_model_dir`, `Pipeline.cancelled` có field nhưng chưa điều khiển hành vi tương ứng. `gemini_api_key` cũng chưa có provider sử dụng.
8. `mix_background` trộn audio nguồn nguyên vẹn, còn giọng gốc; không phải tách instrumental. Khi không có TTS mà bật giữ audio gốc, cùng nội dung nguồn có thể được đưa vào cả hai input mix.
9. `JobQueue.load` giữ nguyên running và không kiểm tra output của done; `save` ghi đè trực tiếp. File lỗi/ghi dở có thể ngăn khởi động hoặc mất lịch sử.

## 4. Backlog đầy đủ theo thứ tự triển khai

**P0:** tính đúng/an toàn dữ liệu. **P1:** parity chức năng cốt lõi. **P2:** trải nghiệm, vận hành và phân phối. Cột Ref trỏ tới mục trong context DichVideo.

### Giai đoạn A — contract, điều phối và an toàn (P0)

| ID | Gap và hiện trạng SubShort | Việc cần làm / phạm vi sửa | Tiêu chí nghiệm thu | Ref |
|---|---|---|---|---|
| G01 | Cấu hình không validation; nguồn/đích bị lẫn | Tách source language và target language; xác định config snapshot cho job; validate tốc độ, volume, merge, mode, đường dẫn/provider trước khi chạy. Sửa `models.py`, `app.py`, `config/settings.json` và caller provider. Quy định rõ volume SubShort hiện là 0–1 khác DichVideo 0–100, migrate có chủ đích | Video Trung → Việt nhận dạng đúng ngôn ngữ nguồn; giá trị ngoài miền bị từ chối trước API/render; job đang chạy không bị config UI mới đổi ngầm | §4, §6 |
| G02 | Batch chạy tất cả job song song, chưa guard start/cancel | Một batch owner tuần tự; trạng thái queued/running/done/failed/cancelled; chống chạy trùng; truyền cancellation xuống FFmpeg, worker, API và vòng TTS; phân biệt lỗi video và lỗi provider cần dừng queue. Sửa `App.run`, `Pipeline`, provider/media runner | Hai job không tải model nặng đồng thời; bấm chạy lặp không nhân đôi job; cancel dừng subprocess sở hữu, giữ intermediate và không báo done | §3, §12 |
| G03 | Queue ghi không atomic, không khôi phục output thiếu | Atomic snapshot nhất quán cả mutation và serialize; recovery khi mở lại; running bị gián đoạn được phục hồi; done chỉ bỏ qua nếu output hợp lệ còn tồn tại. Giữ khả năng đọc lịch sử SubShort hiện có khi đổi schema; phục hồi JSON hỏng và reset error/output/progress cũ khi retry. Sửa `queue.py`, `Job`, UI | Mở lại sau dừng đột ngột không mắc running; output bị xóa được nhận diện; lỗi ghi không phá snapshot tốt trước đó; retry lỗi mới không bị output cũ che mất | §3 |
| G04 | Key UI không lưu; lưu JSON plaintext; HTTP chưa chính sách lỗi | Hoàn thiện nhập/lưu/xóa key bằng cơ chế bảo vệ theo tài khoản Windows; config chỉ giữ preference. Migrate key cũ sau khi lưu an toàn, không log secret. HTTP có timeout, phân loại auth/quota/network/429, retry hữu hạn và hủy được | Key nhập qua UI dùng được sau mở lại; JSON/log không chứa key rõ; lỗi auth dừng batch phù hợp; retry không bỏ qua cancel | §7 |

### Giai đoạn B — tính đúng của nội dung và timeline (P0)

Phụ thuộc: G01–G02; G03 cần xong trước nghiệm thu crash/resume.

| ID | Gap và hiện trạng SubShort | Việc cần làm / phạm vi sửa | Tiêu chí nghiệm thu | Ref |
|---|---|---|---|---|
| G05 | `merge_segments` đếm số segment thay vì word/CJK; dịch theo dòng | Gộp theo word budget có CJK; dịch có ID và schema, kiểm tra đủ/duy nhất ID, text hợp lệ; giữ timestamp/bounds. Giới hạn request đồng thời và backoff dùng G04; bỏ fallback text nguồn im lặng. Sửa `providers.py`, pipeline, config và test contract gộp hiện tại | Đầu ra đảo thứ tự ID vẫn map đúng; thiếu/trùng ID gây lỗi rõ; cue Trung không bị coi cả câu là một word; không publish bản dịch thiếu | §4, §7 |
| G06 | ASR output chưa kiểm tra bounds/text; không nhánh speech không đáng tin | Validate segment hữu hạn, thứ tự, thời gian trong media và nội dung; nhận diện no-speech/không đáng tin theo bằng chứng recognizer, giữ hình/tiếng gốc thay vì sinh lời/hook giả. Probe media và xử lý nguồn không audio. Sửa provider/pipeline/media | Clip im lặng không tạo thoại giả; result malformed bị chặn; nguồn không audio có kết quả/lỗi đã định nghĩa, không crash mơ hồ | §2, §6 |
| G07 | Chưa slow timeline, clip chồng, timing metadata sai scope | Đo duration clip, normalize PCM, xếp `start_i=max(requested_i,end_prev)`; ghép có khoảng lặng không chồng và tránh graph FFmpeg mở mọi clip cùng lúc. Tính alignment theo **start clip cuối** so với `D/slow_speed`; áp tốc độ cuối đồng bộ hình/audio/SRT, pad/trim/freeze đúng. Sửa pipeline, `build_timeline`, media và test timeline hiện tại | Kiểm tra các ca không tràn, clip cuối chỉ vượt end, start clip cuối vượt timeline; audio không chồng/mất chữ; công thức duration đúng; `slow_speed` và `final_speed` cùng có hiệu lực | §8–9 |
| G08 | Luôn burn translated SRT; không ASR giọng cuối/bảo toàn caption | ASR giọng riêng sau final speed, word timing và caption ngắn; đối chiếu nội dung TTS, fallback timing tham chiếu khi ASR cuối lỗi thường; cancel vẫn truyền lên. Xuất SRT/ASS gắn timeline thực. Sửa pipeline/providers/subtitle writer | Câu ASR bỏ sót vẫn có nội dung TTS; caption bám giọng đã xếp, không bám bounds nguồn; không dùng final mix làm nguồn ASR; test lỗi thường khác hủy | §10 |
| G09 | `Job.mode` chưa được dùng | Triển khai ba nhánh: đầy đủ, chỉ dịch+sub, chỉ dịch+dub. Chỉ-sub giữ audio gốc và không TTS/tách nền; chỉ-dub bỏ ASR cuối/caption nhưng hook độc lập. Sửa `Pipeline.run`, media render, UI/settings | Ba mode xuất đúng track/overlay; stage không cần thiết thực sự không chạy; mode chỉ-sub không mix nguồn hai lần | §10 |

### Giai đoạn C — model, nguồn và chất lượng đầu ra (P1)

Các nhánh độc lập có thể triển khai song song sau A; tích hợp cần B.

| ID | Gap và hiện trạng SubShort | Việc cần làm / phạm vi sửa | Tiêu chí nghiệm thu | Ref |
|---|---|---|---|---|
| G10 | ASR mới Whisper fixed small; `asr_provider=firered` lại chọn QwenRuntime không truyền `--firered` | Route rõ Whisper/Qwen3-ASR/FireRed; VAD thay span cứng 60 giây; kiểm chứng protocol worker thật trước tích hợp. Session theo batch, model/device selection, CUDA capability và fallback CPU có chẩn đoán; dùng `qwen_model_dir` thực sự hoặc bỏ field. Sửa provider/pipeline/config/dependency/package | Mỗi lựa chọn gọi đúng engine; sample có khoảng lặng/câu qua biên chunk không mất lời; batch reuse và cancel dọn worker; có bằng chứng GPU/CPU thực chạy. Không suy engine từ tên EXE | §6, §12 |
| G11 | VieNeu chỉ wrapper do người dùng cung cấp; chưa OmniVoice/preset/custom | Tích hợp VieNeu v3 Turbo CPU ONNX và voice preset/ref audio thực; normalize clip. OmniVoice worker cho ngôn ngữ khác, lifecycle/offload/fallback; registry custom voice bằng ID bền vững, kiểm tra ref audio/text. Edge giữ là lựa chọn riêng, không đánh dấu parity model | Chạy được tiếng Việt không cần wrapper tự viết; một ngôn ngữ ngoài Việt qua OmniVoice; preset/custom lưu và mở lại đúng; audio ref ngoài giới hạn bị chặn; TTS generation một owner | §8 |
| G12 | `separate_background` chưa dùng; mix nguồn còn giọng gốc | Tách instrumental thực bằng model/backend được kiểm chứng; để parity model dùng UVR MDX Inst_HQ_3, không coi dependency demucs là bằng chứng đã có. Stereo nền, alignment/tempo theo G07, gain có đơn vị rõ, limiter; volume=0 bỏ separator. Sửa media/pipeline/config/model assets | Nghe A/B xác nhận giảm giọng nguồn; final mix đúng duration, không clipping; volume=0 không tải model; ASR caption vẫn nghe voice riêng | §9 |
| G13 | Chỉ OpenAI chat, chưa Gemini dù có key field | Provider OpenAI/Gemini dùng chung contract ID từ G05; model có thể cấu hình theo API còn hỗ trợ, không hardcode sao chép tên model từ binary như bảo đảm khả dụng. UI chọn provider và báo lỗi rõ | Cùng sample chạy qua cả hai provider, số/ID cue và bounds nhất quán; provider thiếu key bị chặn trước job | §7 |
| G14 | `App.add` chỉ file local; không downloader trong pipeline | Nhập nhiều URL/ID; resolve local/Douyin/YouTube/public Facebook Reel/Drive đúng phạm vi reference. Tải file tạm rồi publish sau kiểm tra nội dung/video; cookie/session YouTube và key dịch vụ bảo vệ như G04; truyền cancel | Mỗi loại nguồn có mẫu tải thật hợp lệ và lỗi private/expired; file rỗng/sai video không vào ASR; URL không bị coi là Path local | §5 |
| G15 | SRT burn đơn giản; không detect/blur sub cũ hoặc styled ASS | Lấy frame mẫu, vision + refinement và diagnostic artifact; chọn mode vùng sub; blur vùng phù hợp, không hứa inpaint. ASS highlight word và font/script đa ngôn ngữ theo G08; tọa độ remap theo layout G17 | Xem frame có/không sub, vùng phát hiện không che nhầm nội dung chính; chữ đa hệ không thiếu glyph; highlight theo word timing; vị trí đúng sau đổi layout | §10 |
| G16 | Chưa có opening hook | Sinh hook từ cue với evidence IDs, validation, theme/seed và thời lượng; AI lỗi dùng trích nội dung đủ tin cậy hoặc bỏ hook. ASS graphics độc lập chế độ caption, không sinh audio | Hook truy về câu nguồn; request lỗi không bịa nội dung; bật/tắt và thời lượng đúng trên video; có artifact nội dung/theme | §10 |
| G17 | Render fixed libx264, `-shortest`, chưa layout/bitrate/probe | Render hình gốc một lần với duration G07; H.264/AAC/faststart; giữ nguyên/dọc/ngang với blur padding. Probe NVENC rồi fallback CPU; bitrate budget đúng encoder. Kiểm tra quote/escape đường dẫn filter trên Windows và publish output sau render thành công | ffprobe xác nhận codec/duration/layout; xem output đường dẫn có khoảng trắng/dấu/colon; NVENC và CPU đều có smoke; FFmpeg lỗi không để output cuối giả thành công | §11 |

### Giai đoạn D — trải nghiệm, phục hồi và phát hành (P2)

| ID | Gap và hiện trạng SubShort | Việc cần làm / phạm vi sửa | Tiêu chí nghiệm thu | Ref |
|---|---|---|---|---|
| G18 | UI ba nút; không settings/mode/progress/resume stage | UI cho contract đã triển khai: nguồn/model/device/provider, voice/ref, mode/speed/merge, nền/layout/sub/hook; progress/log lỗi/cancel, mở output. Reuse intermediate có manifest và kiểm tra source/config liên quan trước reuse SRT/clip/hook. Tách cập nhật Tk khỏi worker | Thao tác hoàn chỉnh không sửa JSON tay; cancel rồi resume không chạy lại phần hợp lệ; đổi source/text/voice/speed invalidate đúng artifact; UI không treo khi model chạy | §3–4, §12 |
| G19 | Không có Drive upload | Service-account + folder riêng, resumable upload, progress/link và xác nhận dung lượng server; giới hạn upload nền, tách lỗi upload khỏi thành công render. Bảo vệ credential, không đưa vào package | Upload file thật, mất mạng rồi tiếp tục đúng; link lưu queue; mở lại hiển thị trạng thái thực; lỗi upload không xóa video local | §11 |
| G20 | Spec `datas=[]`, chưa contract tools/runtime/models; entry script relative import và frozen root chưa được chứng minh | Kiểm chứng entrypoint/root resolution và layout dữ liệu writable của PyInstaller; khai báo FFmpeg/ffprobe/runtime/model discovery hoặc quy trình tải có thông báo, kiểm tra tồn tại/phiên bản. Chọn default phù hợp dependency cài thực tế; đọc giấy phép trước phân phối. Đồng bộ requirements/pyproject và README theo tính năng thực | Build chạy trên Windows sạch không source tree, tìm config/tools/model đúng; chạy local sample qua binary; hướng dẫn cài không hứa adapter chưa tồn tại | §3, §6, §8, §11 |
| G21 | Test/metadata mới ở mức helper, stage list hardcode | Bổ sung test consumer-visible cho A/B, integration boundary worker/API, smoke media/model thật và báo cáo stage timing/error/encoder/model đã dùng; stage list phản ánh nhánh thực. Cập nhật bốn test cũ khi contract gộp/timeline đổi, không giữ semantics cũ để test xanh | Có proof cho checklist §6; không đánh dấu parity từ mock-only hoặc JSON metadata; README phân biệt đã có/chưa có/đã smoke | §13, §15 |

### Nhánh thương mại — gap ghi nhận, không tự động triển khai

- [ ] **G22 — Trial, lượt, đăng ký và credit-gated publish.** SubShort chưa có quota, debit hay pending-credit output; DichVideo có (§11). Với bản dùng cá nhân, đề xuất không sao chép backend/định danh máy. Đây là khác biệt có chủ đích cần người dùng chốt nếu yêu cầu parity thương mại.
- Nếu đưa G22 vào scope: cần backend do dự án sở hữu, chính sách riêng tư và consent; xác nhận debit không trùng, giữ output pending khi kết quả charge chưa rõ, recovery và publish sau confirmed. Không dùng endpoint/secret/identity của DichVideo. Phụ thuộc G02–G04, G17–G18; nghiệm thu cả mất mạng sau charge nhưng trước response.
- An toàn publish file sau render thành công vẫn thuộc G17 kể cả không có G22.

## 5. Phase và task triển khai cụ thể

Các task dưới đây là đơn vị thực hiện; G01–G22 ở mục 4 giữ contract và tiêu chí chi tiết, không phải backlog thứ hai. Trạng thái từng task theo checkbox và bằng chứng của phase. Mỗi task gồm sửa caller/config/test bị ảnh hưởng và ghi bằng chứng nghiệm thu; G21 được thực hiện xuyên suốt, không để cuối mới kiểm tra.

### Phase 1 — Chốt cấu hình và dữ liệu job (P0)

**Phụ thuộc:** không. **Phạm vi:** `models.py`, `config/settings.json`, caller trong `app.py`, test cấu hình/job. **Gap:** G01, G03, G21.

- [x] **P1.1 — Tách ngôn ngữ:** thêm source language độc lập target; ASR nhận source, translator nhận target; đọc được cấu hình cũ.
- [x] **P1.2 — Validate cấu hình:** kiểm tra mode/provider, tốc độ, merge budget, volume và đường dẫn cần cho nhánh được chọn; chốt volume 0–1 và migration, không tự chia mọi giá trị theo phỏng đoán.
- [x] **P1.3 — Snapshot job:** lưu cấu hình có ảnh hưởng kết quả theo job, không chứa secret; pipeline dùng snapshot thay vì settings UI đang thay đổi.
- [x] **P1.4 — Chốt trạng thái:** định nghĩa queued/running/done/failed/cancelled, stage/progress/error/output; reset state cũ khi retry và đọc lịch sử hiện có.

**Kết thúc khi:** nguồn Trung → đích Việt được truyền đúng; cấu hình sai bị chặn trước xử lý; đổi settings không đổi job đang chạy; fixture cấu hình/queue cũ vẫn nạp được.

**Bằng chứng Phase 1 — 2026-11-16:** sửa `models.py` (`Settings.validate/snapshot/for_job`, `Job.prepare`), caller trong `app.py`, cấu hình mặc định và README; thêm `tests/test_settings_jobs.py`. `python -m pytest tests/test_core.py tests/test_settings_jobs.py -q`: **48 passed**. Test pipeline cô lập kiểm tra source `zh` → target `vi`, snapshot qua queue JSON, key hiện tại và invalid config bị chặn trước media. Launch/close `App(Path.cwd())` bằng Tk `after`: thành công. Chưa chạy ASR/API/TTS/render thật hoặc quan sát trực quan UI; bằng chứng này không xác nhận parity model/timeline. Volume cũ hợp lệ 0–1 giữ nguyên; giá trị phần trăm bị từ chối. Provider/mode chưa triển khai và separation bị chặn rõ.

### Phase 2 — Queue bền vững, batch và hủy (P0)

**Phụ thuộc:** Phase 1. **Phạm vi:** `queue.py`, `app.py`, runner subprocess trong `media.py`/`providers.py`, test queue/cancel. **Gap:** G02–G03, G21.

- [x] **P2.1 — Lưu atomic:** khóa mutation và snapshot nhất quán; ghi file tạm rồi replace; khi lỗi ghi giữ snapshot tốt trước đó.
- [x] **P2.2 — Recovery:** xử lý JSON hỏng mà không ghi đè bằng lịch sử rỗng; khôi phục running bị gián đoạn; nhận diện done thiếu output.
- [x] **P2.3 — Một batch owner:** claim job trước dispatch, chạy video tuần tự, chặn start lặp; xác định lỗi nào dừng cả batch và lỗi nào chỉ dừng job.
- [x] **P2.4 — Hủy xuyên pipeline:** truyền tín hiệu qua stage, vòng TTS, request và subprocess; dừng process thuộc job, giữ intermediate; đóng app có lifecycle rõ.
- [x] **P2.5 — Nối UI điều khiển tối thiểu:** start/cancel, trạng thái lỗi và retry không bị output cũ che; worker không gọi Tk trực tiếp.

**Kết thúc khi:** start liên tiếp không chạy trùng; hai job không chạy model đồng thời; cancel không báo done; kill/mở lại và lỗi ghi không làm mất lịch sử tốt.

Bằng chứng Phase 2 — 2026-11-16: `queue.py` dùng RLock, deep-copy snapshot, atomic replace và rollback; lịch sử hỏng khóa ghi, running chuyển failed/interrupted, done mất file chuyển failed. `batch.py` giữ một worker tuần tự, claim trước chạy, snapshot settings lúc Start. `execution.py` truyền cancellation qua ContextVar; media/provider dùng runner sở hữu process tree; OpenAI chạy HTTP worker riêng để hủy request đang chờ, key qua stdin. `app.py` dùng event queue/poll trên Tk main thread, có cancel/retry và chờ worker khi đóng. Auth/quota, thiếu dependency provider và lỗi lưu queue dừng batch; lỗi video thường chỉ dừng job. Kiểm chứng: `python -m pytest tests/test_core.py tests/test_settings_jobs.py tests/test_queue_recovery.py tests/test_queue_process.py tests/test_batch.py tests/test_execution.py tests/test_app_queue.py -q`: **89 passed**. Bao gồm process con/cháu thật, bystander còn sống, fsync/replace lỗi, worker `os._exit` rồi mở lại, cancel trong render không publish output, và Tk mainloop thật start/cancel/retry/close + read-only history hỏng. CLI `--http-worker` round-trip qua data URL local thành công. Bổ sung kiểm chứng sau khi khôi phục lịch poll định kỳ trong `app.py`: `python -m pytest tests/test_app_queue.py -q -s`: **2 passed**; test UI trước đó treo vì `poll()` không tự schedule lần kế tiếp. Chưa build executable, gọi API/model/render thật hoặc xem screenshot UI. Native Whisper chỉ hủy tại ranh giới model/segment; Edge task cancel theo API asyncio, không có abort hook riêng. Retry chạy lại toàn job, không resume intermediate. Queue chỉ đồng bộ trong một instance ứng dụng, không phải khóa liên process.

### Phase 3 — Credential và request an toàn (P0)

**Phụ thuộc:** Phase 1; tích hợp cancel cần P2.4. **Phạm vi:** `models.py`, API dialog trong `app.py`, request trong `providers.py`, test secret/request. **Gap:** G04, G21.

- [x] **P3.1 — Lưu secret theo tài khoản Windows:** Windows DPAPI, nhập/đọc/xóa hai key; config chỉ lưu preference, không lưu key trong snapshot hoặc repr.
- [x] **P3.2 — Migration key cũ:** lưu bản DPAPI trước khi atomic scrub plaintext; lỗi giữ nguyên file cũ; store hỏng không bị ghi đè.
- [x] **P3.3 — Chính sách request:** timeout 30 giây, tối đa 3 lần; retry network/timeout/408/429/5xx, backoff 0.5/1 giây và Retry-After giới hạn 30 giây; auth/quota fatal, cancel cả trong backoff; lỗi không mang response body/key.
- [x] **P3.4 — Nối API dialog:** nhập key mới, trống giữ key, checkbox xóa; không hiện lại key; batch sau dùng credential mới, thiếu key bị chặn trước request.

**Kiểm chứng:** `tests/test_credentials.py`, `test_api_dialog.py`, `test_batch_credentials.py`, `test_settings_jobs.py`, `test_app_queue.py`: **63 passed**. `test_http_policy.py`, `test_execution.py`: **52 passed**. Bao gồm Windows DPAPI thật, migration lỗi giữ legacy, store hỏng không overwrite, Tk dialog lưu/mở lại/xóa/cancel và lỗi lưu, batch giữ credential lúc Start, HTTP worker loopback 200/429/disconnect, retry hữu hạn và cancel backoff, traceback không lộ key. Không gọi API ngoài, build executable, thử credential khác tài khoản/máy, hoặc chụp screenshot. DPAPI không ngăn phần mềm cùng tài khoản đọc key; Gemini chỉ có storage, chưa có pipeline provider (giữ phạm vi Phase 3).

### Phase 4 — ASR và bản dịch có contract chặt (P0)

**Phụ thuộc:** Phase 1–3. **Phạm vi:** `providers.py`, `media.py`, `Pipeline.run`, config và test gộp/dịch/ASR. **Gap:** G05–G06, G21.

- [x] **P4.1 — Probe nguồn:** duration hữu hạn dương, video/audio stream; không audio hoặc ASR rỗng bảo toàn byte nguồn, không dịch/TTS/render.
- [x] **P4.2 — Validate ASR:** timestamp hữu hạn, không âm, start < end, trong duration, start/end có thứ tự; text không rỗng đúng kiểu. Không ép kiểu hoặc bỏ row lỗi âm thầm.
- [x] **P4.3 — Gộp theo budget:** greedy word/CJK budget, giữ bounds và cue đơn vượt budget; `merge_words` mặc định 20 thay field cũ. Config/job cũ `merge_segments=N` được đọc thành budget N; field mới ưu tiên, snapshot mới không giữ key cũ.
- [x] **P4.4 — Dịch theo ID:** JSON schema strict; map đủ/duy nhất ID và text hợp lệ; đảo ID vẫn giữ thứ tự/timestamp nguồn, không fallback text nguồn.
- [x] **P4.5 — Giới hạn dịch đồng thời:** một request tại một thời điểm, dùng timeout/retry/cancel Phase 3; response sai hoặc hủy không đi tới subtitle/TTS/render hay publish thành công.

**Kiểm chứng:** **212 passed** qua `test_provider_contracts.py`, `test_source_contracts.py`, `test_translation_publish.py`, `test_merge_config.py`, `test_settings_jobs.py`, `test_core.py`, `test_http_policy.py`, `test_execution.py`, `test_batch.py`. Có ffprobe/FFmpeg thật tạo video không audio và video audio im lặng, tách WAV thật; recognizer im lặng được thay bằng kết quả rỗng có kiểm tra PCM. Có malformed recognizer, bounds, CJK/mixed/oversized cue, ID đảo/thiếu/trùng/thừa, hủy và chặn publish. Chưa chạy model ASR/API ngoài, build executable hoặc render end-to-end. No-speech phụ thuộc recognizer trả rỗng, chưa xác minh model thật không hallucinate trên im lặng; nhánh bảo toàn bỏ qua speed setting theo thiết kế SubShort.

### Phase 5 — Timeline và render đồng bộ tối thiểu (P0)

**Phụ thuộc:** Phase 4. **Phạm vi:** `build_timeline`, `media.py`, `Pipeline.run`, SRT writer và test timeline. **Gap:** G07, phần duration/publish của G17, G21.

- [x] **P5.1 — Đo và chuẩn hóa TTS:** mono PCM16 24 kHz, duration từ frame thực; timeline lưu requested/actual start/end, duration và delay bằng giây.
- [x] **P5.2 — Xếp giọng không chồng:** start=max(requested,end trước) trên sample grid; streaming từng clip/khoảng lặng, có kiểm tra hủy và PCM lỗi.
- [x] **P5.3 — Alignment:** chỉ stretch khi start clip cuối > D/slow_speed; end-only overrun freeze hình cuối; cascade được giữ đủ giọng.
- [x] **P5.4 — Chung timebase:** hình dùng slow*alignment*final, voice dùng final toàn cục, SRT dùng actual timing/final; pad/trim/freeze rõ duration, không dùng -shortest. Không TTS dùng nguồn gốc và slow*final.
- [x] **P5.5 — Publish an toàn:** render pending MP4, kiểm tra stream/duration bằng ffprobe rồi atomic replace; lỗi/hủy dọn pending, không công bố job done.

**Kiểm chứng:** 162 passed qua `test_timeline.py`, `test_media_timeline.py`, `test_pipeline_timing.py`, `test_core.py`, `test_settings_jobs.py`, `test_source_contracts.py`, `test_translation_publish.py`, `test_execution.py`. Ba ca alignment, delay dây chuyền, chuẩn hóa/tempo, PCM giao clip, duration/video/audio tail ở tốc độ khác 1, render thật với subtitle path có dấu câu; pipeline lỗi render/validation/hủy không publish. ASR/dịch/TTS trong scenario pipeline dùng dữ liệu xác định, FFmpeg/ffprobe thật. Chưa nghe giọng model thật, chạy API hoặc build executable; kiểm tra waveform không thay thế đánh giá chất lượng nghe.

### Phase 5B — Căn lời Việt theo từng đoạn thoại nguồn (P0, đã triển khai; đang nghiệm thu thật)

**Phụ thuộc:** hạ tầng Phase 3–6 và cache Phase 10 hiện có. **Ưu tiên:** làm trước các gate parity còn lại. **Gap liên quan:** G05, G07–G08, G18, G21. Không mở rộng sang xưng hô, diarization hoặc nhiều giọng.

#### Bằng chứng và nguyên nhân

- Người dùng đã nghe/xem output và xác nhận: giọng Việt/phụ đề khớp nhau; không khớp thời điểm lời nguồn. Chưa có nghiệm thu định lượng độ chính xác ASR nguồn.
- `[ARTIFACT]` `output/example1-full-20261117-183250/`: 109 đoạn ASR gộp thành 34 nhóm, `slow_speed=0.7`, `final_speed=1.0`. Mốc đầu clip đúng theo `source_start/0.7`, sai số tối đa khoảng 17,86 microsecond; không có bằng chứng drift do queue trong lượt này.
- `[ARTIFACT]` Nhóm đầu gồm các đoạn bắt đầu tại 0,00 / 5,40 / 12,86 giây nguồn, tương ứng 0,00 / 7,71 / 18,37 giây output. Cả ba ý được tạo thành một clip Việt phát ở 0,00–3,28 giây. Mốc các câu con đã mất trước TTS.
- Source tại thời điểm chẩn đoán: `app.py` gọi `merge_segments` trước dịch; `providers.py:merge_segments` chỉ giữ bounds ngoài và nối text theo word/CJK budget, không xét khoảng nghỉ. `build_timeline` dùng `max(requested, previous_end)`, không fit clip theo lượt nguồn. Caption bám clip Việt nên không sửa được lỗi này bằng offset phụ đề.
- `[INFERENCE]` ASR nguồn có thể còn sai timestamp hoặc gom nhiều lượt trong một segment. Phase này giữ từng segment ASR, không tuyên bố đã nhận diện lượt người nói thật hoặc sửa mọi lỗi khẩu hình.

#### Contract đã chốt

- Giữ ID ổn định và timestamp từng đoạn ASR nguồn xuyên suốt dịch → TTS → timeline; không nối nhiều đoạn thành một clip. Dịch nhiều đoạn trong cùng request để lấy ngữ cảnh, trả text riêng đúng ID.
- Timebase xếp clip trước tốc độ cuối: `start_i = source_start_i / slow_speed`; hạn kết thúc là `source_start_next / slow_speed`, đoạn cuối là `source_duration / slow_speed`. Khi xuất, hình/tiếng/phụ đề cùng áp `final_speed`; không áp hai lần.
- Clip ngắn giữ tốc độ tự nhiên, phần còn lại là khoảng nghỉ. Clip dài tăng tốc vừa đủ, tối đa **1,2×**, giữ cao độ; đo lại WAV sau xử lý để xác nhận vừa cửa sổ.
- Nếu vẫn quá dài: yêu cầu rút gọn giữ ý **một lần cho đoạn đó**, tạo giọng lại, đo và áp cùng giới hạn 1,2×. Một lần là một yêu cầu rút gọn ở mức nghiệp vụ; retry transport hữu hạn vẫn theo chính sách HTTP hiện có. Không đổi provider/model hoặc vượt consent.
- Nếu vẫn không vừa: dừng job, báo ID, bounds, thời lượng clip/cửa sổ và số lần rút gọn. Không cắt tiếng, đẩy các câu sau, kéo hình/freeze để bù hoặc publish video như thành công. Giữ intermediate hợp lệ để chẩn đoán/resume.
- Phụ đề tiếp tục bám giọng Việt **sau** fit; text rút gọn phải được cập nhật đồng bộ trong artifact dịch thực dùng, caption và timeline.
- Nhánh chỉ-sub không qua TTS/fit/rút gọn; giữ tiếng gốc và timestamp từng đoạn. Nhánh chỉ-dub dùng cùng chính sách fit nhưng không sinh caption. Không thêm tùy chọn legacy để quay lại gộp/đẩy câu.

#### Task theo thứ tự triển khai

- [ ] **P5B.1 — Khóa contract và phạm vi caller:** dùng references để rà `merge_segments`, `build_timeline`, translator và caller; rà `models.py`, `default_settings.json`, `settings_dialog.py`, config/snapshot, fixture và README. Bổ sung regression tại seam hiện có cho ba đoạn 0 / 5,40 / 12,86 giây: ba clip riêng giữ đúng start sau đổi tốc độ; kiểm chứng lỗi mất mốc trước khi sửa. Với đoạn nguồn chồng nhau/cùng start, không âm thầm dời hoặc bỏ đoạn: báo lỗi alignment rõ nếu không tạo được cửa sổ dương; không phát minh thuật toán tách người nói.
- [ ] **P5B.2 — Giữ đơn vị dịch/TTS:** bỏ bước gộp làm mất bounds trong pipeline; giữ ID nguồn khi response đảo thứ tự và khi rút gọn một đoạn. Dùng chung validation ID/text hiện có cho OpenAI/Gemini/OpenAI-compatible. Prompt cho phép dùng ngữ cảnh nhưng không chuyển nội dung giữa ID. Loại helper/caller/test gộp trở nên vô dụng; bỏ `merge_words` khỏi UI/template/snapshot mới và config preference khi lưu, vẫn đọc config/job cũ mà không áp lại gộp. Resume job cũ phải chạy contract mới, không khôi phục semantics cũ.
- [ ] **P5B.3 — Fit audio có giới hạn:** trong `media.py` tái sử dụng FFmpeg/runner và PCM normalization hiện có để đổi tempo giữ cao độ; chỉ chạy khi cần. Trong `providers.py`/pipeline tính cửa sổ bằng sample grid 24 kHz nhất quán; kiểm tra PCM và duration thực, không dựa riêng metadata hoặc duration dự đoán. Timeline không dùng `previous_end` để dời start; từ chối clip không vừa. Nhánh có TTS không còn stretch/freeze bù overrun; tái sử dụng đường render duration cố định hiện có.
- [ ] **P5B.4 — Rút gọn hữu hạn:** mở rộng translator tối thiểu để rút gọn đúng ID, có text nguồn, bản dịch hiện tại, ngữ cảnh và ngân sách thời gian; không cam kết LLM ước lượng đúng duration. Pipeline synthesize/đo lại và áp giới hạn sau rút gọn; thất bại rõ nếu vẫn dài. Giữ auth/schema/timeout 120 giây, cancellation và secret redaction; không fallback text nguồn hoặc cắt chữ/tiếng.
- [ ] **P5B.5 — Artifact và resume nhất quán:** cập nhật `app.py`, `artifacts.py` và dependency fingerprint theo policy mới, ID nguồn, text thực dùng, model/voice và cửa sổ fit. Invalidate bản dịch gộp/timeline/caption cũ; reuse ASR và raw TTS chỉ khi dependency thực sự khớp. Phân biệt raw TTS với clip đã fit để đổi speed/window không tempo hai lần. Lưu lần rút gọn đã dùng để resume không gọi thêm ngoài hạn; request đã gửi nhưng kết quả chưa rõ không tự gửi lại rút gọn nghiệp vụ. Timeline ghi source ID/bounds, window, raw/fitted duration, tempo, text cuối và trạng thái rút gọn; không chứa credential.
- [ ] **P5B.6 — Tích hợp output và dọn contract cũ:** caption nhận vị trí/thời lượng/text clip cuối; giữ mapping chung với `final_speed`, background và hình. Cập nhật test timeline/alignment, pipeline timing, provider contract, resume và cấu hình bị đổi; thay test cố định semantics cascade/stretch cũ, giữ kiểm tra validation/PCM/cancel/publish. Cập nhật README và ghi rõ thiết kế mới khác baseline parity lịch sử.
- [ ] **P5B.7 — Kiểm chứng và nghiệm thu:** chạy bộ test tập trung một lần sau tích hợp, rồi scenario nhỏ FFmpeg thật và đoạn đầu `example1` với provider/TTS thật trong thư mục output mới. Sau khi đoạn nhỏ đạt mới chạy toàn `example1` bằng cấu hình/consent đã được phép; không ghi đè input, output hoặc queue người dùng. Ghi command, model/config, API rút gọn, bảng source→output và kết quả nghe/xem; chỉ đóng task khi có bằng chứng tương ứng.

#### Acceptance bắt buộc

- Ba đoạn đầu giữ mốc output xấp xỉ **0,00 / 7,71 / 18,37 giây** ở 0,7×, không đọc cả ba trước 3,28 giây; so vị trí PCM theo sample grid, nghe/xem để xác nhận không nhầm nội dung giữa ID.
- Test clip ngắn / vừa / cần tempo / vượt 1,2× / rút gọn thành công / rút gọn vẫn dài; đo WAV thật sau tempo và kiểm tra `start_i` không bị clip trước thay đổi. Có trường hợp đúng biên làm tròn mẫu, đoạn cuối và speed đầu/cuối khác 1.
- Response đảo ID vẫn map đúng; thiếu/trùng/thừa ID hoặc text rỗng gây lỗi. Hủy trong HTTP/TTS/FFmpeg không tiếp tục render; lỗi fit không publish output hoặc báo done.
- Resume không dùng bản dịch gộp cũ, không fit lại clip đã fit, không reset ngân sách rút gọn; đổi voice/text/speed/window invalidate đúng phần phụ thuộc. Giữ được lịch sử/output cũ, không tự coi chúng đã đạt policy mới.
- Kiểm chứng ba mode, audio/video duration, caption theo clip cuối và không dời timeline hình để giấu overrun. Nghe đoạn đầu và các nhóm cũ 11/24 để xác nhận khoảng nghỉ nguồn được giữ; nghiệm thu nghe/nhìn cuối do người dùng xác nhận.

**Triển khai và kiểm chứng — 2026-11-17:** đã bỏ helper/caller gộp và cấu hình UI/template/snapshot mới; config/job cũ đọc được nhưng bỏ preference gộp. `Segment.source_id` xuyên cache/dịch/rút gọn; `source_windows` và `build_timeline` cố định mốc trên grid 24 kHz, từ chối overlap/overrun. `MediaAdapter.fit_clip` đo PCM thật, dùng `atempo` tối đa 1,2×; ledger atomic giữ ngân sách rút gọn và phân biệt window/voice/model. Raw TTS tách riêng clip fit. README đã cập nhật. Không có LSP trên runtime này; đã dùng tìm kiếm references trong repo.

Kiểm tra: `python -m pytest tests/test_settings_dialog.py tests/test_merge_config.py tests/test_settings_jobs.py -q`: **72 passed** (có mở dialog Tk). Bộ timeline/media/pipeline/resume/provider/publish/consent/visual/core đầu tiên: **170 passed, 5 failed**; sửa validation overflow số vô hạn và fixture/ID contract, sau đó chạy lại đúng ba file lỗi `test_timeline.py test_resume_pipeline.py test_cloud_consent.py`: **81 passed**. Những test đã đạt còn lại gồm FFmpeg tempo thật, ba mode render/decode thật, lỗi/cancel/publish. Chưa thay các checkbox nghiệm thu bằng kết quả fake provider; scenario provider/model thật và nghiệm thu nghe/nhìn ghi riêng khi có kết quả.

Scenario thật đoạn đầu: `.venv/Scripts/python.exe verify_phase5b_temp.py short` (script tạm, xóa sau nghiệm thu), output `output/example1-phase5b-short-20261117-201138/`, **done**, 54,99 giây. Whisper small CPU, OpenAI-compatible `omp-default`, VieNeu v3 Turbo CPU preset Hải Đăng, nền HQ3; dùng snapshot/consent đã có, không ghi đè queue/output cũ. Bốn đoạn riêng ở mốc nguồn 0 / 5,40 / 12,90 / 14,98 giây → output 0 / 7,714292 / 18,428583 / 21,40 giây. Whisper nhận lại đoạn ngắn cho mốc 12,90 thay 12,86 của artifact full cũ; pipeline không sửa timestamp ASR. Tất cả tempo 1,0, không gọi rút gọn, kết thúc trong cửa sổ. Assert PCM grid sai số ≤0,5 sample đạt; ffprobe 32,952 giây, video 1920×1080 có audio. Đã xem `preview.jpg` tại 8,5 giây: phụ đề Việt hiện trên hình thật. Chưa nghe âm thanh để nghiệm thu độ tự nhiên/ý dịch.

Scenario full: `.venv/Scripts/python.exe verify_phase5b_temp.py full`, output `output/example1-phase5b-full-20261117-201256/`. Kết quả **failed đúng policy**, 178,5 giây: ID `107`, bounds nguồn `386,10–386,90s`, cửa sổ `1,142875s`, giọng sau một lần rút gọn `1,44s`; vượt khả năng fit ở 1,2×. Ledger có 5 yêu cầu rút gọn đã completed; ID 107 raw ban đầu 1,20s, sau rút gọn 1,44s (model không bảo đảm câu ngắn hơn sẽ đọc nhanh hơn). Không publish MP4, output rỗng; giữ raw/fit/text/ledger để resume/chẩn đoán. Không chạy lại để tiêu thêm ngân sách hoặc tự tăng tempo. Chưa có full video mới để nghe/xem các nhóm cũ 11/24; **P5B.7 vẫn mở**, không báo full nghiệm thu thành công. Test xác nhận resume không lặp rút gọn đã dùng; chưa chạy resume model thật. Script scenario tạm được xóa sau khi ghi bằng chứng.

Trạng thái P5B.1–P5B.6: code và regression đã triển khai; các checkbox tổng giữ mở ở gate nghiệm thu, không đồng nghĩa chưa có source. P5B.1 dùng báo cáo lỗi và artifact cũ làm repro, không chạy lại implementation cũ để tạo một lượt red nhân tạo. P5B.7 đã thực hiện scenario ngắn/full nhưng chưa đạt điều kiện full xuất video và nghiệm thu nghe của người dùng.

**Điều chỉnh theo yêu cầu người dùng:** nới trần tempo lên **1,4×**, vẫn chỉ rút gọn một lần và không cắt tiếng/dời câu. Nếu lần atempo theo tỷ lệ thời lượng còn dư mẫu, thử lại ở trần 1,4× rồi đo PCM; vẫn quá cửa sổ thì lỗi. Kiểm chứng `python -m pytest tests/test_media_timeline.py tests/test_resume_pipeline.py -q`: **43 passed**. WAV thật ID 107 sau rút gọn 1,44s fit còn **1,036125s** ở tempo 1,4 trong cửa sổ **1,142875s**, không gọi API/TTS lại. Chưa chạy lại full pipeline hoặc nghe đánh giá độ tự nhiên; các kết quả full failed phía trên là lịch sử ở trần cũ, không phải kết quả chính sách mới.

**Scenario full trần 1,4×:** `output/example1-phase5b-full-tempo14-20261117-204708/`, **done**, 523,39 giây. Chạy pipeline thật từ cache copy của lần full trước vào thư mục mới, không ghi đè output cũ. Xuất `example1_translated.mp4`; ffprobe: video 1920×1080 dài 555,166667s, audio/container 555,190s. Kiểm tra 109 dòng timeline: tất cả clip kết thúc trong cửa sổ (sai số cho phép một mẫu PCM), tempo không vượt 1,4×. Đã trích/xem frame 18,8s: phụ đề Việt hiển thị trên hình. Đây là full render thành công theo trần mới; chưa nghe toàn video hoặc xác nhận chất lượng lời dịch/độ tự nhiên, nên gate nghiệm thu nghe vẫn mở.






**Giới hạn/rủi ro:** chưa sửa ASR nguồn, xưng hô, nhận diện người nói, nhiều giọng hoặc karaoke từng từ. Tăng tốc 1,2× cần nghiệm thu độ tự nhiên; rút gọn có thể mất sắc thái nên phải nghe/đọc kiểm tra. Số clip tăng từ 34 về số đoạn nguồn làm tăng số lần TTS; số lần gọi dịch lại có thể tăng chi phí. Không đánh dấu hoàn tất từ metadata hoặc test fake provider.

### Phase 5C — Chất lượng ASR và dịch Trung–Việt (P0, kế hoạch)

**Mục tiêu:** lời Việt dễ hiểu, đúng ý và nhất quán ngữ cảnh; không coi render thành công hoặc JSON hợp lệ là đạt chất lượng. Triển khai trước các gate parity còn lại. Đây là cải tiến SubShort, không phải kết luận về hành vi DichVideo.

**Baseline:** người dùng nghe bản full `output/example1-phase5b-full-tempo14-20261117-204708/` và báo dịch tối nghĩa. Artifact có câu Việt “Em gái nhỏ”, “Hai cái thìa vàng vàng”, “Dầu thơm, công thức nấu ăn”; transcript Trung có từ đáng ngờ `链铁`, `鸡滚油`. Chưa đối chiếu audio nên không tự thay bằng từ phỏng đoán. Prompt dịch hiện dùng toàn bộ items làm context nhưng chưa có quy tắc văn nói/xưng hô/thuật ngữ. Không chốt nguyên nhân chỉ do prompt hay chỉ do ASR.

#### Task triển khai

- [ ] **P5C.1 — Khóa bộ đánh giá thật:** trích đoạn hội thoại đầu khoảng 60–75 giây và các điểm nghi vấn quanh 02:31, 03:23 cùng một đoạn cuối video. Giữ audio gốc, timestamp, ASR Trung, bản Việt trước/sau và text rút gọn theo source ID. Lập bản chép/ý nghĩa tham chiếu bằng nghe đối chiếu; cần người hiểu tiếng Trung xác nhận chỗ không chắc. Không dùng chính LLM tạo bản dịch làm trọng tài duy nhất. Phân loại riêng lỗi nhận dạng, dịch, xưng hô và rút gọn; baseline lấy artifact cũ, không chạy lại toàn pipeline cũ.
- [ ] **P5C.2 — Chọn ASR theo chất lượng tiếng Trung:** so Whisper small hiện tại với Qwen/FireRed mà `ASRSession` đã hỗ trợ, ép ngôn ngữ `zh` cho scenario Trung. Kiểm tra model/runtime/license thực có, đo cùng audio, CER trên phần tham chiếu đã xác nhận, tên/món ăn bị sai và thời gian/RAM/VRAM. Chọn backend theo kết quả nghe/đọc, không tự đổi mặc định của mọi ngôn ngữ hoặc fallback khác model khi lỗi. Nếu chưa backend nào đạt thì gate ASR chưa đóng; không che sai bằng dịch đoán.
- [ ] **P5C.3 — Dịch hội thoại theo ngữ cảnh:** cải thiện prompt chung cho provider hiện có: tiếng Việt văn nói tự nhiên, thuật ngữ nấu ăn theo ngữ cảnh, tên riêng nhất quán, không bịa quan hệ/giới tính hoặc thêm ý; khi chưa biết quan hệ dùng cách diễn đạt trung tính. Giữ từng ID và bounds; cho phép hiểu câu qua nhiều đoạn nhưng không dồn nội dung sang ID khác. Giới hạn request theo khả năng model; video dài chia lô có context lân cận và bảng thuật ngữ/xưng hô dùng chung, chỉ trả ID được yêu cầu. Transcript là dữ liệu, không phải chỉ dẫn. Không thêm lượt LLM hậu kiểm mặc định chỉ để làm văn xuôi trôi chảy.
- [ ] **P5C.4 — Bản dịch có thể duyệt/sửa:** lưu riêng ASR gốc, transcript hiệu chỉnh, bản dịch đầy đủ và bản rút gọn thực đọc. Tái dùng màn hình chỉnh sửa hiện có nếu có; nếu chưa có, thêm thao tác tối thiểu xem/sửa theo ID, không sửa timestamp ngoài ý muốn. Chỉnh từ nguồn phải invalidate bản dịch phụ thuộc; sửa bản Việt invalidate TTS/caption/render tương ứng. Không sửa trực tiếp artifact cache như một API ngầm. Rút gọn chỉ sau bản dịch đầy đủ, giữ ý/tên/xưng hô; vẫn một lần và tempo tối đa 1,4×.
- [ ] **P5C.5 — Contract/cache và tích hợp:** trước sửa dùng references rà `ASRSession`, translator, `Segment`, pipeline, artifacts, settings/snapshot/UI và test. Version hóa dependency dịch theo prompt/policy, context, glossary và bản hiệu chỉnh để không reuse bản tối nghĩa cũ. Reuse audio/ASR hợp lệ khi chỉ đổi dịch; không dùng bản rút gọn làm bản dịch gốc. Giữ consent, auth, cancel, budget và redaction hiện có; thay provider/model chỉ theo cấu hình được chọn.
- [ ] **P5C.6 — Kiểm chứng:** test tập trung ID thiếu/trùng/đảo, context nhiều lô, sửa transcript/dịch rồi resume, hủy/lỗi không publish, giữ timing và tách full/shortened text. Dịch thật bộ mẫu trước, người dùng/người hiểu Trung duyệt ý nghĩa và xưng hô; sau đó mới TTS/render đoạn ngắn và full vào output mới. Ghi model/prompt policy, chi phí/lượt API, thời gian và kết quả riêng; test schema không chứng minh dịch đúng.

**Bằng chứng triển khai 2026-11-18 (chưa nghiệm thu chất lượng):** bộ mẫu nguyên tốc độ tại `output/dialogue-quality-20261118/` gồm opening 0–75s, cooking-terms 145–163s, addressing 198–216s và ending 375–388s; `samples.json` giữ baseline và trạng thái chưa có bản chép Trung được xác nhận. Whisper small CPU hoàn tất bốn mẫu trong lần lượt 13,891 / 1,500 / 1,781 / 2,281 giây; FireRed CPU hoàn tất trong 70,218 / 1,750 / 6,516 / 19,109 giây, lưu riêng `whisper-comparison.json` và `firered-comparison.json`. Qwen CPU bị chặn bởi validation timestamp aligner ở mẫu đầu; không ép mốc/drop text để công bố thành công. Hai backend hoàn tất vẫn bất đồng về từ món ăn và câu ngắn; chưa có ground truth nên không tính CER hoặc chọn backend thắng trên example1.

**Kiểm chứng dịch theo context và alignment:** chạy tập trung `test_translation_context.py`, `test_provider_contracts.py`, `test_gemini.py`, `test_openai_compatible.py`, `test_qwen_alignment.py`: **90 passed** sau khi bỏ assertion pin wording prompt. Chẩn đoán Qwen trên audio opening tìm thấy nhiều row aligner có start=end (ví dụ `嗯` tại 0–0 giây trên đoạn 0,412 giây); giữ lỗi chi tiết, không clamp/drop text hoặc đoán alignment. Test contract không phải bằng chứng chất lượng dịch thật. Chưa chạy dịch/TTS/full mới trước khi duyệt mẫu.

**GPU theo yêu cầu người dùng:** cấu hình local đặt `asr_device="cuda"`, `tts_device="cuda"`, giữ nguyên model/provider. RTX 5060 Ti 16 GB, driver 621.14. Đã cài runtime cuBLAS/cuDNN trong `.venv`; smoke `cooking-terms.wav` qua `ASRSession` quan sát model CTranslate2 `device="cuda"`, device index `[0]`, compute type `float16`, transcript `哇 油香十足`, 18,813 giây gồm khởi tạo, không diagnostic/fallback CPU. Không dùng thời gian cold-start này để kết luận GPU nhanh hơn CPU. TTS GPU và Qwen/FireRed CUDA chưa được xác nhận tại thời điểm ghi bằng chứng này.

**Chốt VieNeu CUDA 2026-11-18:** đã sửa runtime `.venv-vieneu` bị trộn torch/torchaudio và hạ transformers về 4.57.6 phù hợp SDK. Worker thật load → synthesize → close trả thành công, exit 0; cùng snapshot/cache VieNeu v3 Turbo, preset Hải Đăng, backend PyTorch, engine CUDA, bfloat16 trên RTX 5060 Ti. WAV mono PCM16 48 kHz có **34.560 frames**; bằng chứng `output/gpu-smoke/acceptance.json`, `worker.log`, `vieneu_cuda.wav`. Runtime thực dùng torch/torchaudio 2.11.0+cu128, trong khi SDK khai báo 2.8.0: smoke chứng minh máy này, không chứng minh mọi cấu hình/phiên bản. Không đổi provider/model, không fallback CPU. Qwen CUDA cũng trả segment Trung trên mẫu AISHELL sau fallback đọc WAV khi thiếu torchcodec (`artifact://860`); chưa có bằng chứng FireRed CUDA hoặc full pipeline GPU mới. Không dùng smoke ngắn làm nghiệm thu chất lượng giọng/dịch.


**Gate hoàn tất:** mọi câu nghi vấn trong bộ mẫu có đối chiếu nguồn và bản Việt đã được duyệt; không còn lỗi nghiêm trọng đảo ý/bịa ý/nhầm món hoặc xưng hô trong mẫu. Lỗi chưa xác định phải hiện rõ, không đánh dấu đạt. Full được người dùng nghe/xem xác nhận; timing/fit/cancel/resume không regression. Không bắt test pin một câu dịch duy nhất khi nhiều cách diễn đạt đều đúng.

### Phase 5D — Nhận diện người nói và lồng tiếng nhiều giọng (P0, kế hoạch)

**Mục tiêu:** cùng một người giữ một giọng Việt xuyên video; người khác được gán giọng riêng có thể kiểm tra/sửa. Không nhân bản giọng nguồn, suy đoán danh tính hay tự luân phiên nam/nữ theo câu. Giữ mode một giọng hiện có cho job cũ và video không bật nhiều giọng; bật nhiều giọng không được âm thầm xuất một giọng khi nhận diện lỗi.

**Baseline:** `Segment` hiện chỉ có start/end/text/source_id; `app.py` truyền cùng `settings.tts_voice` cho tất cả clip. Chưa có diarization hoặc speaker-to-voice map. Phụ thuộc timing Phase 5B; dùng bản dịch đạt Phase 5C để nghiệm thu kết hợp. Spike diarization có thể làm song song với P5C.1–P5C.2.

#### Task triển khai

- [ ] **P5D.1 — Thử backend diarization local:** lấy audio nguồn nguyên tốc độ, thử ứng viên đầu tiên pyannote speaker diarization trong worker riêng để cách ly dependency torch. Kiểm tra license, quyền tải/model/token và CPU/CUDA trước khi cài/chạy; không tự nhận license hoặc gửi audio lên cloud. So nhãn người nói với phần tham chiếu nghe thật: chuyển lượt, cùng người tái xuất hiện, giọng ngoài hình, nhạc/nhiễu và nói chồng. Ghi speaker confusion, lỗi bỏ/thừa người, runtime và tài nguyên; không suy ra giới tính/quan hệ từ cluster. Chốt version/model/runtime bằng kết quả này trước khi tích hợp; không thêm nhiều backend dự phòng khi chưa cần.
- [ ] **P5D.2 — Contract lượt nói:** thêm speaker ID ổn định trong phạm vi job và artifact lượt nói riêng; giữ source ID liên kết bản ASR trước khi tách. Gán speaker theo giao thời gian với lượt nguồn. Nếu một ASR segment chứa nhiều người, tách theo ranh giới người nói và nhận dạng/căn lại text trên audio con, không chia chữ theo tỷ lệ thời gian. Lượt quá ngắn hoặc không xác định gắn trạng thái cần duyệt; không tự gán một speaker chắc chắn. Không hứa speaker ID có ý nghĩa danh tính ngoài job.
- [ ] **P5D.3 — Duyệt nhãn và ánh xạ giọng:** UI tối thiểu cho nghe mẫu từng speaker, đổi/gộp/tách nhãn sai theo lượt và chọn giọng từ VoiceRegistry/provider hiện có. Lưu map speaker→voice/reference cùng snapshot job, cho phép cùng người dùng một giọng nhất quán cả khi tái xuất hiện. Nhiều giọng phải có map đầy đủ, các speaker dùng chung giọng chỉ khi người dùng chủ động chọn. Không đoán tuổi/giới tính hay tự trích clone giọng nguồn. Chặn chạy với nhãn cần duyệt/map thiếu và báo rõ lượt cần xử lý.
- [ ] **P5D.4 — Nói chồng và timing:** giữ timestamp từng lượt. Với nhiều speaker nói chồng, fit từng clip trong cửa sổ của lane speaker (lượt kế tiếp cùng speaker hoặc hết video), mix các lane tại đúng mốc bằng PCM có kiểm soát peak, không nối cascade/đẩy start hoặc bỏ một người. Cùng speaker có lượt chồng bất khả thi phải yêu cầu sửa nhãn/bounds. Caption hỗ trợ khoảng chồng có vị trí phân biệt, không gộp text hai người thành một câu. Nếu backend không giải được nguồn nói chồng thì báo cần duyệt, không công bố đã xử lý đúng.
- [ ] **P5D.5 — Truyền speaker xuyên pipeline:** cập nhật Segment/serialization/validation, ASR-turn alignment, translator/rút gọn, TTS dispatch, caption/timeline, config/UI/defaults và queue snapshot. Dịch nhận nhãn người nói ổn định và xưng hô đã duyệt, không để LLM tự đổi nhãn. Cache phụ thuộc diarization model/policy/audio, turn mapping, speaker/voice/reference và text; đổi một giọng chỉ synthesize lại clip bị ảnh hưởng, rồi mix/caption/render lại phần phụ thuộc. Raw TTS và fit vẫn tách; thay map không reset hoặc né ngân sách rút gọn trên lượt không đổi. Job/cache cũ đọc được mà không giả tạo nhãn người nói đã xác minh.
- [ ] **P5D.6 — Kiểm chứng và nghiệm thu kết hợp:** regression cho hai người luân phiên/tái xuất hiện, ASR một đoạn nhiều người, speaker chưa rõ, overlap, sửa/gộp nhãn và đổi voice rồi resume; kiểm tra clip gọi đúng giọng, start không drift, không mất lượt, giới hạn 1,4×/rút gọn/cancel/publish giữ nguyên. Chạy đoạn hội thoại mẫu bằng diarization và ít nhất hai giọng thật; nghe xác nhận người↔giọng, độ rõ và mức âm. Đoạn nhỏ đạt rồi mới full `example1`, ffprobe/decode/xem caption và người dùng nghe nghiệm thu; không chỉ đếm voice ID trong metadata.

**Gate hoàn tất:** các lượt trong mẫu đã đối chiếu nguồn, một người không đổi giọng ngẫu nhiên và không gán lẫn người; nhãn chưa rõ không bị che. Full dịch dễ hiểu và giọng phân vai được người dùng xác nhận. Chỉ-sub không gọi TTS; chỉ-dub không tạo caption; job một giọng vẫn hoạt động. Không ghi đè media/model/credential/output cũ.

**Thứ tự thực hiện:** P5C.1–P5C.2 và spike P5D.1 → P5C.3–P5C.6 → P5D.2–P5D.6 → nghiệm thu full kết hợp. Nếu diarization cần quyền tải/license hoặc thiếu phần tham chiếu Trung đã xác nhận, hoàn tất phần làm được và ghi đúng gate còn thiếu; không thay bằng mock/fallback một giọng rồi báo xong.

**Triển khai tích hợp 2026-11-18 (chưa nghiệm thu nhiều giọng thật):** đã nối worker diarization local, speaker ID/turn review, voice map, nhận dạng lại cue giao nhiều speaker, cache theo nhãn/giọng và mixer lane PCM. Màn hình duyệt giữ ID/bounds, cho sửa transcript/bản dịch và nghe sample; bản dịch đầy đủ tách bản thực đọc. Sửa transcript invalidate dịch; sửa bản Việt invalidate TTS phụ thuộc. Metadata review còn sau job done. Đã sửa trạng thái subtitle và cửa sổ dialog để nút lưu không bị khuất. Scenario Tk thật mở review, sửa bản Việt, bấm lưu và kiểm tra snapshot queue đã lưu theo ID đạt; ảnh `D:/tmp/subshort-review-smoke.png`.

Kiểm chứng tập trung: `test_diarization_integration.py test_subtitles.py`: **23 passed**, có FFmpeg mix hai WAV overlap và assert mẫu PCM trước/trong/sau overlap. Bộ `test_diarization_integration.py test_resume_pipeline.py test_settings_jobs.py test_asr_session.py test_qwen_alignment.py`: **83 passed, 1 failed**; lỗi còn lại là fixture FireRed chưa cung cấp dependency torch cho loader mới, không phải kết quả inference thật. Resume từ job done sửa transcript/dịch và cancel/publish đã đạt trong lượt này. Chưa có runtime/model pyannote đã provision và quyền license phù hợp để chạy mẫu thật; không tự chấp nhận license/tải model hoặc dùng mock làm bằng chứng nhận diện hai người. P5D.1/P5D.6 và gate nghe/full vẫn mở.

Chốt regression sau sửa fixture: ASR/alignment **20 passed**, test FireRed interval còn lỗi được sửa mock ghi nhận chiều dài WAV rồi chạy riêng **1 passed**. Audio fallback chạy bằng interpreter `.venv-firered` với torch/torchaudio thật: **5 passed, 2 failed** do fixture bit-depth/VAD path; sửa bit-depth thành 40 bit và tạo VAD fixture, chạy lại đúng hai test **2 passed**. Không thêm fallback production để chiều test double. Runtime FireRed không có pytest nên mượn pytest từ site-packages Python hệ thống; torch/torchaudio vẫn lấy trong `.venv-firered`.



### Phase 6 — Ba mode và caption theo giọng thực (P0)

**Phụ thuộc:** Phase 5. **Phạm vi:** pipeline/provider/media, subtitle writer, mode selector và test nhánh. **Gap:** G08–G09, G21.

- [x] **P6.1 — Dispatch mode:** đầy đủ/chỉ-sub/chỉ-dub; chỉ-sub giữ audio gốc, không TTS/separation; chỉ-dub không final ASR/caption.
- [x] **P6.2 — Nhận dạng voice cuối:** ASR riêng voice sau final speed, không dùng final mix; Whisper local session trả word timing để tạo caption ngắn, backend không có word alignment giữ fallback timing tham chiếu.
- [x] **P6.3 — Bảo toàn nội dung caption:** đối chiếu TTS text, fallback timing tham chiếu khi ASR lỗi thường/bỏ sót; không nuốt cancellation.
- [x] **P6.4 — Xuất caption thực:** SRT/ASS dùng vị trí giọng đã xếp; metadata stage chỉ ghi nhánh thực chạy.

**Kết thúc khi:** ba output có đúng audio/overlay; stage thừa không chạy; caption bám giọng và không mất câu khi final ASR lỗi thường.

Bằng chứng triển khai Phase 6 — 2026-11-17: `ASRSession.transcribe_words` gọi Whisper local-only với `word_timestamps=True`, chuẩn hóa qua validator; pipeline truyền riêng `voice_final`, không dùng final mix. Kiểm chứng: `python -m pytest tests/test_asr_session.py tests/test_pipeline_timing.py -q`: **27 passed**; bộ caption/mode trước đó: **111 passed**. FFmpeg/ffprobe thật kiểm tra ba mode và publish lỗi/hủy; model ASR thật, ASS/highlight và nghe/xem vẫn chưa nghiệm thu.


### Phase 7 — Model và provider thật (P1)

**Phụ thuộc:** Phase 2–4; tích hợp output dựa Phase 5–6. Các nhánh ASR/TTS/nền/translation có thể làm song song khi không cùng sửa một file. **Phạm vi:** provider/media/pipeline, model/config/dependency liên quan. **Gap:** G10–G13, G21.

- [x] **P7.1 — Xác minh worker:** đã chạy `QwenRuntime/QwenWorker.exe --firered --device cpu`; worker nhận protocol stdin nhưng lỗi vì thiếu request file, không chứng minh route model. Không hồi sinh executable-name routing.
- [ ] **P7.2 — ASR đầy đủ:** route Whisper/Qwen/FireRed, model/device selection, VAD thay chunk cứng; kiểm tra câu qua biên chunk và khoảng lặng. Source đã có route trực tiếp, VAD interval và CUDA fallback cùng model; còn thiếu sample provider thật.
- [ ] **P7.3 — Lifecycle ASR:** session theo batch, reuse model, CUDA capability/CPU fallback có chẩn đoán và cancel dọn worker; diagnostic đã nối từ `Pipeline` tới UI và caption chỉ gọi word timing cho Whisper. Còn thiếu bằng chứng model/GPU thật.
- [x] **P7.4 — VieNeu:** tích hợp v3 Turbo CPU ONNX, preset/ref thật, normalize clip; không yêu cầu người dùng tự viết wrapper. Proof preset/reuse/device tại `vieneu-cpu-smoke.txt`, custom registry/normalize/inference tại `vieneu-custom-smoke.txt`; chưa nghiệm thu chất lượng nghe.
- [ ] **P7.5 — OmniVoice và custom voice:** worker cho ngôn ngữ ngoài Việt; offload/fallback, registry ID bền vững, validate ref audio/text; giữ một owner TTS.
- [ ] **P7.6 — Nền instrumental:** tích hợp separator thật; kiểm chứng UVR MDX Inst_HQ_3 cho parity model, stereo/gain/limiter và tempo theo Phase 5; volume=0 không tải model.
- [ ] **P7.7 — Gemini:** dùng contract ID Phase 4, lựa chọn model/provider và credential Phase 3; lỗi provider rõ, không tự đổi provider im lặng.

**Bằng chứng tích hợp P7 — 2026-11-17:** `asr.py`, `tts.py`, `tts_worker.py` đã được rà soát qua session/worker contract; `Pipeline` reuse session theo batch, cập nhật callback diagnostic theo job và giữ CPU fallback trong cùng model. Kiểm chứng regression: `python -m pytest tests/test_asr_session.py tests/test_tts_worker.py tests/test_pipeline_timing.py tests/test_settings_jobs.py -q`: **83 passed**. Smoke thật đã bổ sung Whisper speech/word/reuse/GPU/fallback, Qwen CPU alignment/reuse, FireRed CPU/reuse/VAD offset, VieNeu CPU/custom/cancel và HQ3 CPU; đã có thêm sample tiếng Trung cho Whisper/Qwen/FireRed, nhưng nghiệm thu tích hợp production, chất lượng Whisper, OmniVoice, Gemini, Qwen/FireRed CUDA, hủy native ASR và nghiệm thu chất lượng vẫn mở.

**Smoke Whisper CPU thật — 2026-11-17:** faster-whisper 1.2.1 tải model `Systran/faster-whisper-small` từ cache SubShort nhưng decode WAV lỗi với PyAV 19.0.2 (`metadata_errors` đã bị upstream loại bỏ). Extra `local` thêm `av<19`; cài lại chọn PyAV 18.1.0. Chạy `ASRSession.transcribe` trên WAV im lặng 2 giây, mono PCM16 16 kHz: trả `[]`, assertion pass (`whisper-cpu-compat-smoke.txt`). Đây chỉ là proof load model/decoder/VAD không sinh chữ trên im lặng; chưa chứng minh nhận dạng câu nói, word timing, reuse hoặc GPU. P7.2/P7.3 vẫn mở.

**Whisper speech/word/reuse CPU — 2026-11-17:** tạo WAV tiếng Anh offline bằng Windows SAPI với text tự soạn, không dùng media/giọng DichVideo hoặc gửi dữ liệu ra ngoài. Model small nhận đủ ba câu, gồm các từ kiểm tra `weather/clear/speech/recognition/subtitles`; `transcribe_words` trả word bounds dương, có thứ tự và giữ từ khóa. Hai lần gọi cùng `ASRSession` giữ cùng model instance; `close` xóa reference. Assertion pass (`whisper-speech-smoke.txt`, sample `whisper-sapi-sample.wav`). Không thay thế smoke tiếng Trung AISHELL-3, nghiệm thu chất lượng Whisper, cancel native inference hoặc các provider/GPU khác; P7.2/P7.3 vẫn mở.

**ASR language/CUDA check — 2026-11-17:** Qwen upstream 0.0.6 nhận canonical language name, không nhận ISO code như `en`/`zh` từ settings; `ASRSession._speech_transcribe` chuyển code trước khi gọi SDK. Regression `python -m pytest tests/test_asr_session.py -q`: **18 passed**, gồm ISO/canonical/auto và VAD offset. CUDA Whisper phát hiện 1 GPU nhưng inference trên sample SAPI lỗi thiếu `cublas64_12.dll` (`whisper-gpu-smoke.txt`); không fallback che thiếu runtime, chưa đạt GPU inference. Qwen isolated runtime đã cài; lần chạy đầu load model/aligner nhưng thiếu audio backend của Silero, đang provision backend, chưa ghi nhận inference thành công.

**Whisper GPU inference thật — 2026-11-17:** thêm thư mục CUDA runtime có sẵn `../DichVideo/_internal/GPU` vào PATH của riêng process kiểm chứng (chỉ đọc, không copy/sửa DLL), preload cuBLAS bằng ctypes; `ASRSession` CUDA nhận đúng ba câu sample SAPI, giữ từ khóa `weather/recognition/subtitles`, device vẫn `cuda` (`whisper-gpu-path-smoke.txt`). `os.add_dll_directory` riêng lẻ không đủ cho loader này; cài SubShort mặc định vẫn cần runtime CUDA 12/cuDNN tương thích trên PATH, không tự phụ thuộc thư mục DichVideo. Đây là smoke inference GPU, không phải nghiệm thu Windows sạch hoặc tiếng Trung.
**Whisper CPU fallback thật — 2026-11-17:** chạy process con với `CUDA_VISIBLE_DEVICES=-1`, `ctranslate2.get_cuda_device_count()==0` và gọi `ASRSession("whisper", cùng model local, "cuda")`. Session giữ nguyên model path, phát đúng diagnostic `CUDA unavailable; loading the same ASR model on CPU`, chuyển `device=cpu` và nhận dạng đủ câu speech mẫu; không monkeypatch, tải model hay đổi môi trường toàn cục. Proof `whisper-cpu-fallback-smoke.txt`. Đây là fallback cùng model trên CPU, chưa chứng minh tiếng Trung hoặc hủy native inference.

**VieNeu device diagnostic — 2026-11-17:** worker luôn gọi VieNeu v3 Turbo ONNX CPU nhưng trước đây báo theo preference CUDA. `tts_worker.py` nay trả device CPU và diagnostic đúng nhánh; test reuse/synthesize tham số CPU/CUDA, `python -m pytest tests/test_tts_worker.py -q`: **12 passed**. Test SDK fake chỉ chứng minh protocol/diagnostic, không thay cho inference VieNeu thật.

**Qwen CPU inference/alignment thật — 2026-11-17:** môi trường `.venv-qwen` dùng torch/torchaudio 2.6.0 sau khi bản audio backend mới lỗi `torchcodec` DLL Windows. Chạy offline `ASRSession` với Qwen3-ASR-0.6B, Qwen3-ForcedAligner-0.6B và Silero VAD local trên sample SAPI tự soạn: nhận câu tiếng Anh, trả word timestamp dương có VAD offset; assertion có `weather` và mọi `0 <= start < end` pass (`qwen-hardware-smoke.txt`). Smoke tiếng Trung AISHELL-3 riêng đã đạt exact transcript/CER 0% (`chinese-asr-smoke.txt`); chưa kiểm chứng Qwen CUDA, cancel native inference hoặc chất lượng trên video thật; P7.2/P7.3 vẫn mở.

**VieNeu CPU ONNX inference thật — 2026-11-17:** `.venv-vieneu` (vieneu 3.8.4, onnxruntime 1.32.0) chạy `TtsSession` → worker offline với snapshot/cache DichVideo chỉ đọc, preset SDK Adam bựa và hai câu Việt tự soạn. WAV mono 48 kHz dài 4.0/3.2 giây, finite/non-silent, peak 0.6531/0.5852; cùng worker PID qua hai lần synthesize, close exit 0. Protocol thật xác nhận chọn CUDA báo CPU và diagnostic đúng (`vieneu-cpu-smoke.txt`). WAV kiểm chứng đã xóa, chưa nghe hoặc đánh giá phát âm; chưa nghiệm thu custom voice/OmniVoice.

**OmniVoice chưa chạy — 2026-11-17:** config hiện tại `consent_omnivoice_license=false`, model/runtime/cache chưa cấu hình; chưa thấy snapshot OmniVoice/Higgs hoặc SDK trong các runtime đã kiểm tra. Model pretrained CC-BY-NC; audio tokenizer có Boson Higgs Audio 2 Community License gồm điều khoản Meta Llama 3. Không tự chấp thuận, cài/download hoặc chạy inference. Cần người dùng chấp thuận đúng giấy phép và provision snapshot đầy đủ (gồm `audio_tokenizer/`) trước smoke với reference tự soạn; đây là blocker nghiệm thu, không phải lỗi protocol đã được chứng minh.

**HQ3 CPU inference thật — 2026-11-17:** `separate_background` chạy audio-separator 0.48.0/ONNX Runtime 1.32.0 trong `.venv-hq3` với cache local và sample SAPI + nhạc sine tự soạn. Output stereo 44.1 kHz, 281950 frame (6.393424 giây), finite, peak 0.03854, không clipping; adapter 16.844 giây (`hq3-hardware-smoke.txt`). Upstream thiếu khai báo `audioread`, librosa 1.0.1 không còn kéo dependency này: thêm `audioread>=3.1,<4` vào extra separation sau repro import lỗi; import SDK thật pass với 3.1.0. Media thử đã xóa; chưa nghe A/B hoặc nghiệm thu chất lượng tách nền.

**FireRed CPU inference thật — 2026-11-17:** `.venv-firered` + SDK revision `834635e4cf277ed8ca92049fc375b17c3dc20748`, model AED-L và Silero VAD local nhận đúng ba câu SAPI tự soạn qua `ASRSession` trong 19.688 giây (`firered-cpu-smoke-compatible.txt`). Assertion meaningful text/từ khóa và interval trong duration 9.243 giây pass; timestamp là VAD segment, không phải word alignment. Runtime đã kiểm chứng torch/torchaudio 2.5.1 CPU, numpy 1.26.4, transformers 4.46.3, peft 0.13.2, accelerate 1.1.1, silero-vad 6.2.3. Resolver không pin nâng torch lên 2.15 và upstream `torch.load` từ chối checkpoint argparse.Namespace; không patch loader/trust override. Torch 2.5.1 vẫn dùng pickle checkpoint: chỉ dùng model tin cậy. Smoke tiếng Trung AISHELL-3 riêng đã đạt exact transcript/CER 0% (`chinese-asr-smoke.txt`); chưa FireRed CUDA hoặc đánh giá chất lượng.

**VieNeu custom reference thật — 2026-11-17:** reference SAPI Microsoft David tự soạn 6.224 giây (không dùng giọng người dùng) được `VoiceRegistry` normalize 24 kHz, lưu/mở lại đúng ID và transcript. Resolve reference rồi VieNeu worker CPU offline synthesize câu Việt: mono 48 kHz, 203520 frame/4.24 giây, finite/non-silent, peak 0.91263, close exit 0 (`vieneu-custom-smoke.txt`). VieNeu nhận ref_audio, không nhận ref_text; transcript giữ ở registry. Đã xóa media/registry tạm; chưa nghe phát âm hoặc đánh giá độ giống giọng.

**FireRed reuse/VAD offset thật — 2026-11-17:** hai lần transcribe cùng sample qua một `ASRSession` giữ cùng model/VAD object và trả cùng transcript/timestamp. Fixture sample + 2 giây digital silence + sample dài 20.4859375 giây trả sáu interval hợp lệ; block sau giữ offset với sai số dưới 150 ms. `close()` xóa reference model/VAD (`firered-reuse-offset-smoke.txt`, 53.625 giây). Chưa chứng minh reclaim bộ nhớ native, cancel trong inference, CUDA hoặc tiếng Trung.

**Qwen reuse thật — 2026-11-17:** hai lần transcribe CPU offline cùng `ASRSession` giữ cùng model/VAD object; cả hai giữ từ khóa weather/speech và aligned bounds trong 9.243 giây. `close()` xóa reference model/VAD (`qwen-reuse-smoke.txt`, 46.984 giây). Không chứng minh reclaim allocator native, cancel inference, CUDA hoặc tiếng Trung.

**VieNeu cancel worker thật — 2026-11-17:** gửi câu Việt dài tự soạn vào worker CPU offline, timer chờ request write/flush rồi hủy sau 1 giây khi worker còn sống. Nhận `Cancelled`, không trả success/không tạo WAV, worker exit 1 và session xóa process; close tiếp theo sạch (`vieneu-cancel-smoke.txt`). Không instrument chính xác operation ONNX tại thời điểm timer; xác nhận dispatch và dừng process thật, không suy native cancellation ASR.

**Fixture tiếng Trung local — 2026-11-17:** SAPI và registry Desktop/OneCore/WOW6432Node chỉ có David/Mark/Zira, language `409` (en-US); HKCU không có voice bổ sung (`firered-chinese-smoke.txt`). Không tạo audio Trung bằng giọng Anh hoặc dùng media người dùng để lấp gate; sau đó dùng WAV AISHELL-3 công khai có license rõ cho smoke offline, được ghi ở mục kế tiếp.
**Smoke ASR tiếng Trung local — 2026-11-17:** dùng đúng WAV AISHELL-3 được ghim digest `e5f8e2417a89ce2418c59d419be999b46206890d1f094b76f77ad4d41b365bca`, có attribution/license trong `output/asr-chinese-acceptance/NOTICE-AISHELL-3.txt`. Chạy offline bằng `ASRSession` trong process CPU riêng: Qwen3-ASR-0.6B trả đúng câu kỳ vọng, CER 0%, 11 đoạn alignment hợp lệ; FireRed AED-L trả đúng câu kỳ vọng, CER 0%, khoảng VAD hợp lệ; Whisper small trả nội dung gần đúng nhưng CER 36.36% (4/11). Bằng chứng đầy đủ về model/runtime/package, command, digest và giới hạn ở `output/asr-chinese-acceptance/chinese-asr-smoke.txt`. Đây là smoke provider độc lập, chưa phải pipeline production tiếng Trung và không đóng P7.2/P7.3 hay nghiệm thu chất lượng.

**Kết thúc khi:** có sample thật cho từng engine/provider, VieNeu Việt và OmniVoice ngoài Việt; preset/custom mở lại đúng; CPU/GPU/fallback có bằng chứng riêng; nghe A/B nền và kiểm tra clipping/duration. Smoke tiếng Trung hiện đã đủ cho Qwen/FireRed và có giới hạn rõ cho Whisper; vẫn thiếu production flow tiếng Trung, OmniVoice và nghiệm thu nghe.

### Phase 8 — Nhập nguồn trực tuyến (P1)

**Phụ thuộc:** Phase 2–4. Có thể song song Phase 5–7. **Phạm vi:** nhập nguồn UI, downloader và bước resolve trong pipeline, config/secret liên quan. **Gap:** G14, G21.

- [ ] **P8.1 — Phân loại nguồn:** nhập nhiều URL/ID/local; resolve trước khi tạo Path cho media, báo input không hợp lệ rõ.
- [ ] **P8.2 — Downloader:** nối Douyin/YouTube/public Facebook Reel/Drive; cookie/session và key dịch vụ dùng cơ chế bảo vệ Phase 3.
- [ ] **P8.3 — Hoàn tất tải an toàn:** file tạm, kiểm tra nội dung/video trước publish; cancel dọn download thuộc job, không xóa input người dùng.
- [ ] **P8.4 — Nghiệm thu từng nguồn:** sample được phép dùng, private/expired, tải lỗi và nội dung không phải video; ghi giới hạn dịch vụ thực tế.

**Source/resume contract check — 2026-11-17:** `python -m pytest tests/test_sources.py tests/test_source_contracts.py tests/test_source_pipeline.py tests/test_resume_pipeline.py -q`: **64 passed**. Bao gồm phân loại URL/prefix ID, chặn host giả, tải vào scratch/probe trước publish, cancel/error cleanup và nhánh giữ nguyên source; resume có fake provider kiểm tra reuse sau cancel, invalidate theo source/model/text/voice/speed và consent không bị cache vượt qua. Chưa download từng dịch vụ thật hoặc private/expired; không coi test downloader fake là nghiệm thu P8.4.

**Kết thúc khi:** mỗi loại nguồn có đường UI → tải → probe → pipeline thật; URL không bị xử lý như đường dẫn local.

### Phase 9 — Hình ảnh, subtitle và hook (P1)

**Phụ thuộc:** Phase 5–6; dùng provider/model Phase 7 theo nhánh. **Phạm vi:** media/render/subtitle/hook, settings và caller UI. **Gap:** G15–G17, G21.

- [x] **P9.1 — Layout:** giữ nguyên/dọc/ngang với blur padding; remap tọa độ overlay từ một contract hình học rõ. Render thật ba layout, manual blur theo source, ASS/hook và portrait→landscape đã xem frame; proof `output/P9 Kiểm tra hình ảnh/`.
- [x] **P9.2 — Encoder:** H.264/AAC/faststart, bitrate budget theo encoder; probe NVENC và fallback CPU có chẩn đoán; giữ render hình gốc một lần.
- [ ] **P9.3 — Vùng sub cũ:** lấy frame mẫu, vision/refinement và diagnostic artifact; auto/manual region, blur phù hợp layout, không gọi blur là inpaint.
- [x] **P9.4 — Styled ASS:** word highlight theo Phase 6, font/script đa ngôn ngữ và remap vị trí; kiểm tra escape filter path Windows. Đã xem render Việt/Trung/Nhật/Arabic/Hebrew/mixed, ba layout và sửa RTL qua libass; proof ngay dưới phase, không cam kết mọi external ASS player.
- [ ] **P9.5 — Hook:** nội dung truy được evidence IDs, validation/theme/seed/duration; lỗi AI dùng trích đoạn đủ tin cậy hoặc bỏ; graphics độc lập caption, không sinh audio.

**Bằng chứng source/integration P9 — 2026-11-17:** `media.py` kiểm tra kích thước trước layout và lưu diagnostic NVENC fallback; `subtitles.py` escape line separator ASS và chặn font phá format; `hooks.py` ghi/kiểm tra `evidence_ids` và không tạo hook khi không có candidate. Kiểm chứng: `python -m pytest tests/test_render_profiles.py tests/test_subtitles.py tests/test_hooks.py tests/test_visual_pipeline.py -q`: **75 passed**. Chưa có vision old-subtitle detection, playback Unicode/ASS hoặc đánh giá hình ảnh; P9.3/P9.5 vẫn mở.

**Smoke NVENC hardware — 2026-11-17:** `nvidia-smi` quan sát RTX 5060 Ti 16 GB; `MediaAdapter.render(..., encoder="nvenc", bitrate_mbps=2)` render nguồn testsrc2/sine tự tạo dài 1 giây bằng `h264_nvenc`. ffprobe/assert xác nhận H.264/AAC, duration sai lệch <0.05 giây và moov trước mdat; `last_encoding.selected=nvenc`, không fallback (`nvenc-hardware-smoke.txt`). Kết hợp với smoke CPU fallback bên dưới đã phủ hai nhánh encoder; chưa phải nghiệm thu hình ảnh, tất cả layout hoặc pipeline model qua NVENC.
**Smoke encoder fallback thật — 2026-11-17:** wrapper process-local chỉ từ chối probe `h264_nvenc` rồi ủy quyền mọi lệnh render cho FFmpeg thật. `MediaAdapter` chọn `libx264`, giữ diagnostic fallback; ffprobe xác nhận H.264/AAC, duration 1 giây, faststart (`moov` trước `mdat`) và `validate_output` pass. Input lỗi thật sinh `MediaError` và không để output giả. Kết hợp với `nvenc-hardware-smoke.txt` là bằng chứng hai nhánh encoder; wrapper cố tình mô phỏng probe unavailable nên không thay thế outage phần cứng. Proof `encoder-fallback-smoke.txt`.

**Triển khai P9.3 — 2026-11-17 (chưa nghiệm thu):** `subtitle_region.py`, Settings/UI và pipeline có off/manual/auto mặc định off, consent frame riêng trước sampling/provider, 3 frame deterministic giới hạn 640×360, transport HTTP hiện có, contract no-subtitle/box nghiêm ngặt, refinement đo biên pixel trong proposal, diagnostic PNG/JSON gồm timestamp/proposed/refined/final region. Renderer nhận region chuẩn hóa nguồn trước layout; không có fallback box hoặc inpaint. Kiểm chứng focused do parent chạy: `python -m pytest tests/test_subtitle_region.py tests/test_cloud_consent.py tests/test_settings_jobs.py tests/test_render_profiles.py tests/test_visual_pipeline.py -q` → **102 passed in 4.37s**. Cấu hình người dùng `config/settings.json` giữ nguyên; field mới thiếu được nạp từ defaults của Settings, không cần migrate/ghi đè cấu hình. Còn cần vision thực được người dùng cho phép, subtitle/no-subtitle footage thật và quan sát output/refinement; P9.3 giữ unchecked.

**Quan sát render P9 local — 2026-11-17:** scenario thật tại `output/P9 Kiểm tra hình ảnh/` chạy manual region blur, ASS karaoke tiếng Việt và hook trích evidence với đường dẫn Windows Unicode/khoảng trắng. Original 640×360, portrait 1080×1920, landscape 1920×1080 đều H.264/AAC yuv420p, 4.000 giây; validate_output/assert pass, CPU libx264 2 Mbps. Agent xem frame bằng image reader: sub cũ bị blur, scene ngoài vùng giữ sắc nét, dấu tiếng Việt không có missing glyph thấy được, highlight vàng/trắng và hook xuất hiện 0.8 giây/biến mất 2.8 giây, padding portrait đúng. Proof `render.log`, `results.json`, `acceptance.txt` và frame/output được giữ. Chưa playback toàn clip, vision thật, chọn UI hoặc pipeline model đầy đủ; blur không bảo đảm redaction an toàn.

**P9 RTL/padding bổ sung — 2026-11-17:** render portrait→landscape giữ foreground sắc nét và blur hai bên; Chinese/Japanese hiển thị glyph qua fallback font Windows (Noto chưa cài). Phát hiện Arabic karaoke đảo thứ tự token do ASS legacy Encoding=1; `subtitles.write_ass` dùng Encoding=-1 để libass layout bidi toàn câu. Giữ logical UTF-8, karaoke và timing, không đảo chuỗi. Focused `python -m pytest tests/test_subtitles.py -q`: **18 passed in 0.27s**; bốn render thật xác nhận Arabic/Hebrew/mixed Latin-number/multiline và English-first đúng thứ tự, highlight Arabic tiến từ từ đầu bên phải. Proof `output/P9 Kiểm tra hình ảnh/acceptance.txt`, `edge-acceptance.txt`, `fixed-bidi-comparison.png`. Chưa bảo đảm VSFilter/external ASS player hoặc subword sweep RTL; renderer dự án là libass.

**Kết thúc khi:** xem frame/output thật cho layout/blur/highlight/hook; Unicode/khoảng trắng/colon trong path chạy được; ffprobe xác nhận codec/duration và có smoke CPU/NVENC riêng.

### Phase 10 — UI đầy đủ và resume intermediate (P2)

**Phụ thuộc:** contract Phase 1–9 theo tính năng; UI tối thiểu đã làm ở Phase 2–3. **Phạm vi:** `app.py`, settings/job/queue, artifact manifest và pipeline reuse. **Gap:** G18, G21.

- [x] **P10.1 — Bề mặt cấu hình:** nối source/model/device/provider, voice/ref, mode/speed/merge, nền/layout/sub/hook vào hành vi đã có; lưu/mở lại đúng.
- [x] **P10.2 — Theo dõi job:** stage/progress/lỗi có ích, mở output/thư mục và retry; cập nhật Tk trên UI thread, không treo khi chạy model.
- [x] **P10.3 — Manifest intermediate:** ghi source/config/content/model liên quan từng artifact; xác định điều kiện reuse SRT/clip/hook, không dựa chỉ vào file tồn tại.
- [ ] **P10.4 — Resume:** cancel rồi tiếp tục từ artifact hợp lệ; đổi source/text/voice/speed invalidate đúng phần phụ thuộc, không dùng nhầm kết quả cũ.

**UI thật P10 — 2026-11-17:** chạy source `cli.main()` với state tạm, thao tác native Windows mouse/keyboard trên Tk thật. Quan sát các tab ASR/dịch/TTS/old-subtitle; lưu manual region `[0.05,0.75,0.9,0.2]`, mở lại giữ đúng và consent frame vẫn false. Queue fixture local running được phục hồi failed/interrupted, done mất output thành failed trên UI lẫn JSON. Proof log/PNG được giữ ở `output/P10 UI acceptance/`; không dùng credential, media người dùng hoặc cloud. Đây chưa là cancel/resume pipeline model đủ stage qua UI, nên P10 chưa nghiệm thu toàn bộ.
**Local production ASR manifest — 2026-11-17:** chạy `Pipeline` thật với video MP4 tạo cục bộ từ `whisper-sapi-sample.wav`, Whisper small CPU và `tts_provider=none/mode=subtitle`; không gửi dữ liệu ra ngoài. FFmpeg probe/extract và ASR thật tạo `output/Local acceptance manifest/output/local-manifest-job/artifacts.json` với một entry `asr` có fingerprint/hash. Khi `consent_translation=false`, cả lần đầu và retry dừng đúng ở stage `translate` với lỗi consent; không tạo SRT hoặc output cuối, manifest giữ nguyên qua retry. Artifact JSON đầy đủ ở `output/Local acceptance manifest/acceptance.json`. Artifact progress mới: `_stage()` trong `subshort/app.py` lưu stage/progress qua `JobQueue` tại các ranh giới pipeline; focused regression `python -m pytest tests/test_pipeline_timing.py tests/test_resume_pipeline.py -q` → **16 passed**, gồm progress tăng dần và cancel giữ stage `tts`/progress trước retry. Đây chỉ là proof source/test; chưa đóng cancel/resume đầy đủ qua UI model pipeline hay nghiệm thu chất lượng. 
**Regression local tập trung — 2026-11-17:** `python -m pytest tests/test_upload_queue.py tests/test_upload.py tests/test_drive.py tests/test_drive_dialog.py tests/test_app_queue.py tests/test_settings_dialog.py tests/test_resume_pipeline.py tests/test_pipeline_timing.py -q` → **68 passed trong 7.63 giây**. Phạm vi gồm metadata upload/queue recovery, contract Drive dialog/worker, wiring settings/UI queue, resume reuse và stage timing. Chưa chứng minh authorization Google, mất mạng/resume thật với Drive hoặc nghiệm thu model/chất lượng.


**Kết thúc khi:** chạy đủ flow không sửa JSON tay; quan sát UI thật; cancel/resume và đổi cấu hình có bằng chứng artifact nào reuse/tạo lại.

### Phase 11 — Upload Drive (P2)

**Phụ thuộc:** credential Phase 3, publish Phase 5 và trạng thái UI Phase 10. Có thể phát triển độc lập model/render. **Phạm vi:** uploader, job/queue và UI upload. **Gap:** G19, G21.

- [ ] **P11.1 — Cấu hình Drive:** service-account/folder riêng; bảo vệ credential, validate quyền và không đưa credential vào package.
- [ ] **P11.2 — Resumable upload:** giới hạn upload nền, progress/cancel và tiếp tục sau mất mạng; xác nhận dung lượng server.
- [ ] **P11.3 — Lưu kết quả:** link/trạng thái upload qua lần mở app; lỗi upload tách khỏi render thành công, không xóa video local.

**Kết thúc khi:** upload file thật, ngắt mạng/tiếp tục và mở lại app vẫn hiển thị kết quả đúng.

### Phase 12 — Đóng gói và nghiệm thu phát hành (P2)

**Phụ thuộc:** các phase chức năng đã hoàn tất; không bỏ qua nhánh chưa có bằng chứng. **Phạm vi:** `cli.py`, `packaging/subshort.spec`, requirements/pyproject, config/assets discovery và README. **Gap:** G20–G21.

- [x] **P12.1 — Entrypoint/layout:** chốt source/frozen root, thư mục state writable và entrypoint không lỗi relative import; không ghi vào thư mục bundle chỉ đọc.
- [ ] **P12.2 — Tài nguyên và dependency:** bundle hoặc chỉ dẫn tools/runtime/model nhất quán; kiểm tra tồn tại/phiên bản, default phù hợp cài đặt; kiểm tra giấy phép trước phân phối.
- [ ] **P12.3 — Smoke binary:** Windows sạch không source tree; mở UI, lưu config/queue và chạy sample local qua binary với đường dẫn Unicode/khoảng trắng.
- [ ] **P12.4 — Nghiệm thu chéo:** thực hiện toàn bộ checklist mục 6 bằng input/config cố định; ghi stage timing/error/model/encoder thực, nghe/xem output thay vì chỉ metadata.
- [x] **P12.5 — Chốt tài liệu:** README nêu rõ đã có/đã smoke/giới hạn, cách cài model/tool và recovery; cập nhật bằng chứng theo G01–G21, chỉ đánh dấu task đạt tiêu chí.

**Bằng chứng dependency P12 — 2026-11-17:** `pyproject.toml` khai báo extras `local` (Whisper/soundfile), `separation` (audio-separator CPU 0.48), giới hạn discovery vào package `subshort`; `requirements-ml.txt` dùng cùng extras, bỏ Demucs không được pipeline sử dụng. Build wheel bằng `pip wheel --no-deps` thành công; assert kiểm tra wheel chỉ chứa `subshort`/dist-info và metadata có cả hai extras. `pip install --dry-run --ignore-installed --only-binary=:all: "audio-separator[cpu]>=0.48,<0.49"` giải được dependency trên Python 3.12 Windows hiện tại. Metadata upstream Qwen 0.0.6 yêu cầu transformers 4.57.6, OmniVoice 0.2.1 yêu cầu >=5.3.0: chưa có môi trường chung tương thích; không quảng bá extra gom mọi model. Chưa cài/chạy HQ3, build executable hoặc smoke Windows sạch; P12 vẫn mở.

**Bằng chứng template P12 — 2026-11-17:** template sạch chuyển vào `subshort/default_settings.json`, khai báo package-data và dùng chung cho wheel/PyInstaller; config người dùng tại `config/settings.json` giữ nguyên. `tests/test_paths.py`: **10 passed**, gồm không ghi đè config và frozen state writable. Wheel build thành công; giải nén vào thư mục tạm và chạy Python `-I` ngoài checkout, `initialize_config` tạo config lần đầu thành công (`packaging-wheel-result.txt`). README sửa đúng artifact `dist/SubShort.exe`, PATH/đường executable thay lời hứa tự tìm `tools/`, và yêu cầu cài SubShort vào interpreter worker ngoài. Đây chưa là smoke executable Windows sạch.

Extra `drive` khai báo `google-auth[requests]>=2.62.2,<3` theo metadata PyPI, cung cấp hai import auth mà uploader sử dụng; wheel metadata assertion pass (`packaging-drive-result.txt`). Chưa gọi Google Drive hoặc dùng credential; P11 giữ mở.

**Smoke executable P12 — 2026-11-17:** `python -m PyInstaller --noconfirm packaging/subshort.spec` build thành công `dist/SubShort.exe`. Copy executable sang thư mục tạm có Unicode/khoảng trắng, chạy `--diagnose --state-dir <thư mục Unicode>` ngoài checkout: exit 0, tạo config với consent false, tìm FFmpeg/ffprobe qua PATH (`packaging-binary-smoke.txt`). Launch UI và đóng bằng WM_CLOSE: exit 0. Capture riêng cửa sổ bằng PrintWindow (`packaging-window-smoke.png`) xác nhận bảng queue và các nút cấu hình/nguồn/retry/upload hiển thị. Đây là máy phát triển hiện tại, không phải Windows sạch; chưa có sample pipeline qua binary nên P12.3 vẫn mở.

**Binary pipeline hiện tại — 2026-11-17:** rebuild current source thành công (`packaging-current-build.txt`), copy executable vào thư mục Unicode/khoảng trắng ngoài checkout. Qua file picker thật enqueue MP4 xanh tự tạo 1 giây không audio, chạy hàng đợi: done/no_audio, 100%, segments=0/preserved_source=true. ffprobe đọc output, SHA-256 source/output cùng `9016d2b6276ea23e60dd5de5b5fdd4ace273f5a877376268c31bcf4a6efba9ec`. Mở lại binary vẫn hiển thị done/no_audio và output; lần reopen/close exit 0. Proof tại `output/P10 UI acceptance/`; config/state tạm, cloud consent false. Chỉ nhánh no-audio trên máy phát triển, chưa model pipeline hoặc Windows sạch; P12.3 giữ mở.

**Rebuild sau sửa RTL — 2026-11-17:** PyInstaller exit 0 trong 77.69 giây; executable SHA-256 `ff905813d939a0529ea46e94b568831e19b480d2880cfeefee042f0530f49052`. Copy ngoài checkout Unicode/khoảng trắng cùng hash; `--diagnose` exit 0 tìm FFmpeg/ffprobe, UI thật hiện và đóng exit 0. Proof `output/P10 UI acceptance/current-binary-log.txt` và `current-binary-window.png`. Không chạy lại pipeline trong smoke này; nhánh no-audio ở lần build trước không bị dùng làm bằng chứng pipeline bản mới.

**Kiểm chứng regression/package bổ sung — 2026-11-17:** shared Tk fixture cho `test_api_dialog.py` loại bỏ việc mỗi test tạo root riêng; `python -m pytest tests/test_api_dialog.py tests/test_settings_dialog.py -q` → **9 passed**. Bộ regression không-GUI hiện tại → **327 passed in 20.48s**. `python -m pip wheel . --no-deps --wheel-dir output/final-wheel-check` build wheel hiện tại thành công (`subshort-0.1.0-py3-none-any.whl`, SHA-256 `7aa1254d686e2386543093bb42b28e8856675fcd14b4d2815e57bc86c8135a31`).

**Kết thúc khi:** binary độc lập chạy được và checklist có bằng chứng; nhánh thiếu môi trường/credential/hardware được ghi là chưa nghiệm thu, không báo parity đầy đủ.

### Phase 13 — Thương mại, chỉ khi được đưa vào scope

**Phụ thuộc:** người dùng chấp thuận G22; Phase 2–3, 5 và 10. **Gap:** G22. Không chặn bản dùng cá nhân.

- [ ] **P13.1 — Contract thương mại:** backend do dự án sở hữu, trial/quota/đăng ký, chính sách riêng tư và consent; không dùng endpoint/secret/identity DichVideo.
- [ ] **P13.2 — Debit và pending output:** chống charge trùng, recovery kết quả charge chưa rõ; chỉ publish sau confirmed.
- [ ] **P13.3 — Nghiệm thu giao dịch:** mất mạng sau charge trước response, mở lại app, retry không debit hai lần và không mất output pending.

**Kết thúc khi:** giao dịch/recovery có bằng chứng và chính sách dữ liệu rõ; nếu không chọn scope thương mại, giữ G22 là khác biệt có chủ đích.

**Thứ tự mặc định:** lịch sử Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12; **hiện ưu tiên Phase 5B trước các gate còn mở**. Chỉ tách nhánh song song theo phụ thuộc ghi ở từng phase; Phase 13 là tùy scope. Không cần sao chép tên file, cấu trúc module hoặc prompt DichVideo từng byte; giữ hành vi, ý nghĩa dữ liệu và khả năng chẩn đoán khác biệt.

## 6. Checklist nghiệm thu parity

- [ ] Local Trung → Việt đủ ba mode; một ngôn ngữ ngoài Việt; preset và custom voice.
- [ ] Nguồn im lặng/không audio; segment lỗi; bản dịch thiếu/trùng/đảo ID; API auth/quota/429.
- [ ] Phase 5B: giữ start từng đoạn nguồn, không gộp/đẩy dây chuyền; cửa sổ tới đoạn kế tiếp/hết video; tempo ≤1,2×, rút gọn một lần rồi lỗi rõ nếu không vừa; speed đầu/cuối khác 1 và resume đúng policy. Các ca cascade/stretch Phase 5 là bằng chứng contract lịch sử, không phải nghiệm thu thiết kế mới.
- [ ] ffprobe đối chiếu duration audio/video; nghe phần giao câu và cuối video; xem caption word highlight/blur/hook/layout, không chỉ đọc JSON.
- [ ] Whisper/Qwen/FireRed route đúng; CPU/GPU/fallback có bằng chứng từng nhánh; batch reuse và cancel dọn process.
- [ ] Tắt nền không chạy separation; bật nền nghe A/B; chỉ-sub giữ tiếng gốc; chỉ-dub không caption ASR.
- [ ] Start lặp, cancel, kill app, mở lại queue, output bị xóa, resume sau đổi config; snapshot tốt không bị mất.
- [ ] URL của từng nguồn; upload Drive resumable; không lộ key/credential/định danh trong JSON/log/package.
- [ ] Windows binary chạy độc lập, đường dẫn Unicode/khoảng trắng; libx264/NVENC, output profile và media hợp lệ.

Dùng một sample được phép sử dụng, giữ nguyên input và config để so sánh hai app. Mốc lịch sử DichVideo 42 ASR → 20 TTS → 33 caption chỉ là quan sát một lượt, không phải số lượng phải ép SubShort đạt. Chất lượng ASR/TTS phụ thuộc model/worker; cần so nội dung và timing, không so số segment đơn thuần.

## 7. Bằng chứng còn thiếu và cách cập nhật plan

- Nội bộ worker DichVideo chưa được giải mã đầy đủ; hợp đồng adapter trong context là baseline, không suy thuật toán ẩn. Cần kiểm chứng protocol worker có sẵn trước khi hứa parity G10/G11.
- Chưa xác nhận dịch vụ tải/API/model trong binary còn khả dụng. Thay dịch vụ/model chỉ khi giữ contract và ghi rõ khác biệt, không báo là cùng implementation.
- File `output/qwen_result_live.json`, model và `QwenRuntime/` hiện diện không chứng minh pipeline hiện tại đã chạy đúng engine end-to-end.
- Khi đóng một gap: ghi ngày, symbol/file đổi, command/scenario đã chạy và kết quả quan sát ngay dưới ID; để checkbox mở nếu chỉ có source mà chưa có nghiệm thu cần thiết.
- Giữ file này là backlog parity; chi tiết hành vi DichVideo tiếp tục nằm ở context reference, tránh hai nguồn mô tả lệch nhau.
