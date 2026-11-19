# SubShort

Ứng dụng desktop Windows để tải/nhập video, nhận dạng lời nói, dịch, tạo phụ đề, lồng tiếng và xuất video.
Giao diện desktop dùng theme studio tối tùy chỉnh: sidebar công cụ, bảng hàng đợi có thanh cuộn, thống kê trạng thái và thanh thao tác riêng. Cài đặt chia theo danh mục ở sidebar; API, giọng nói và Drive dùng cùng bảng màu. Không đổi pipeline, credential hay định dạng lịch sử. File picker và thanh tiêu đề vẫn dùng Windows để giữ tích hợp hệ thống.
Chọn một hoặc nhiều job rồi bấm **Xóa đã chọn** để bỏ khỏi lịch sử hàng đợi sau khi xác nhận. Không xóa video nguồn hoặc kết quả; không cho xóa khi đang xử lý/tải lên. Sau khi đổi dịch vụ API, xóa job cũ và thêm lại video để dùng cấu hình mới.

## Cấu trúc dự án

```text
SubShort/
├── src/subshort/       # Package ứng dụng, worker và default_settings.json
├── tests/              # Kiểm thử
├── packaging/          # Entrypoint và spec PyInstaller
├── docs/               # Backlog parity và glossary
├── config/             # Cấu hình/credential local, không đóng gói
├── logs/               # Log runtime
├── output/             # Video kết quả
├── output_artifacts/   # Intermediate và bằng chứng chạy
├── pyproject.toml      # Metadata, dependency, package discovery và pytest
└── requirements.txt    # Danh sách dependency runtime cho pip
```

Source dùng convention `src` layout; giữ namespace `subshort.*` và worker trong package để chạy bằng `python -m`. Không chia thêm tầng thư mục khi chưa cần. Tài liệu: [backlog parity](docs/PLAN_PARITY_DICHVIDEO.md), [glossary](docs/GLOSSARY.md).

Model/runtime (`ModelASR/`, `models/`, `QwenRuntime/`), media mẫu `example/`, `.venv*`, `build/`, `dist/` và artifact có sẵn giữ nguyên vị trí để không phá cấu hình hoặc bằng chứng cũ. Chúng không phải source package; file sinh ra và credential local được loại khỏi Git qua `.gitignore`.

Sau khi chuyển từ layout cũ, chạy lại `python -m pip install --no-deps -e .` trong từng môi trường đã cài editable SubShort (ứng dụng và worker). Cấu hình/queue/output của bản source vẫn nằm ở gốc dự án, không chuyển vào `src/`. Bản exe có sẵn không đổi; cần build lại để nhận source mới.

Kiểm thử: cài `pytest` vào `.venv`, rồi chạy `.\.venv\Scripts\python -m pytest`. Cài editable trước khi chạy CLI từ ngoài checkout; không dựa vào thư mục hiện tại để import package.


## Chạy từ source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
# Thêm Whisper local (không bao gồm model):
# .\.venv\Scripts\python -m pip install -e ".[local]"
.\.venv\Scripts\python -m subshort.cli
```

Cần cài `ffmpeg` và `ffprobe` vào `PATH`, hoặc đặt đường dẫn executable tuyệt đối trong Cài đặt. Ứng dụng chưa tự tìm thư mục `tools/` cạnh executable.

Extra `local` cài faster-whisper/soundfile; `separation` cài audio-separator CPU (nhánh 0.48). Model/cache vẫn phải provision riêng. Không cài Qwen, FireRed, VieNeu hoặc OmniVoice bằng hai extra này. TTS dùng interpreter riêng qua `tts_python`; Qwen và OmniVoice cần môi trường tách biệt vì metadata upstream hiện yêu cầu `transformers==4.57.6` và `transformers>=5.3.0`. Dependency cài được không chứng minh model chạy đúng hay quyền phân phối model.

Extra `separation` khai báo thêm `audioread`: audio-separator 0.48.0 import package này nhưng metadata upstream không khai báo, còn librosa 1.0.1 không kéo nó transitively. HQ3 CPU đã chạy trên sample SAPI + sine tự soạn; waveform hợp lệ không thay cho nghe A/B chất lượng tách nền.

Extra `local` giới hạn `av<19`: faster-whisper 1.2.1 còn gọi `av.open(metadata_errors=...)`, tham số đã bị PyAV 19 loại bỏ. Cài lại `python -m pip install -e ".[local]"` nếu gặp lỗi decoder này.

VieNeu CPU ONNX đã synthesize hai câu Việt tự soạn qua worker offline, reuse session và xác nhận diagnostic CPU khi preference CUDA. VieNeu CUDA PyTorch cũng đã synthesize thật qua worker với cùng snapshot/cache: `output/gpu-smoke/acceptance.json` và `vieneu_cuda.wav` ghi RTX 5060 Ti, `backend=pytorch`, `device=cuda`, dtype `torch.bfloat16`, 34.560 mẫu ở 48 kHz. Runtime đã kiểm chứng là torch/torchaudio `2.11.0+cu128`, transformers `4.57.6`; VieNeu metadata khai báo CUDA extra torch/torchaudio `2.8.0`, nên bản 2.11.0 chỉ là cấu hình đã smoke trên máy này, không phải tương thích upstream được bảo đảm. Nếu cài lại, giữ các gói CUDA cùng một bộ và kiểm tra inference thật; không chỉ dựa vào nhãn device. Đây là proof thực thi; chưa nghiệm thu nghe, chất lượng tiếng Trung hoặc mọi voice. OmniVoice chưa chạy vì chưa có consent giấy phép và snapshot/runtime; không tự tải hoặc chấp thuận thay người dùng.
Qwen CPU đã smoke offline với `qwen-asr==0.0.6`, `transformers==4.57.6`, `silero-vad==6.2.3`, `torch==2.6.0` và `torchaudio==2.6.0` trong môi trường riêng. Cặp torch/torchaudio mới hơn ở lần thử này kéo `torchcodec` rồi lỗi DLL Windows; không coi đó là cấu hình đã kiểm chứng. Cấu hình đường dẫn model ASR, forced aligner và Silero VAD local; chạy ứng dụng bằng Python của môi trường Qwen vì ASR không dùng `tts_python`.
FireRed AED-L CPU đã smoke qua Silero VAD với SDK revision `834635e4cf277ed8ca92049fc375b17c3dc20748`, torch/torchaudio 2.5.1 CPU, numpy 1.26.4, transformers 4.46.3, peft 0.13.2, accelerate 1.1.1 và silero-vad 6.2.3. Đặt checkout SDK trên `PYTHONPATH` của Python chạy ứng dụng. Resolver mới không pin có thể nâng torch và làm upstream checkpoint loader lỗi. Loader torch 2.5.1 dùng pickle: chỉ mở model có nguồn tin cậy; chưa có smoke FireRed CUDA/tiếng Trung.

Upload Drive cần extra `drive`: `python -m pip install -e ".[drive]"`. Cài dependency không cấp quyền upload; mỗi lượt vẫn cần xác nhận riêng. Không đưa service-account JSON vào package.

### Dịch qua OpenAI-compatible

Trong **Cài đặt API**, chọn dịch vụ `openai_compatible`, nhập **Base URL**, **Model ID** và key ở dòng **OpenAI-compatible API key**. Ví dụ Base URL: `http://127.0.0.1:1234/v1` hoặc `https://provider.example/v1`. Ứng dụng tự nối `/chat/completions`; không nhập cả endpoint này. Base URL không được chứa user/password, query hoặc fragment. Chỉ dùng HTTP cho server local/tin cậy; HTTP không mã hóa key và nội dung.

Key được bảo vệ bằng Windows DPAPI, không ghi vào snapshot job. Để ô key trống để giữ key hiện có; chọn Xóa key để xóa. Server local không yêu cầu xác thực có thể dùng key giả như `local`. Thêm job mới để dùng cấu hình vừa lưu. Provider phải hỗ trợ `response_format: json_schema` và trả nội dung Chat Completions; không tự fallback sang dịch vụ khác khi lỗi. Thay Base URL làm mất hiệu lực cache bản dịch. Lựa chọn này chỉ áp dụng dịch văn bản, không đổi provider vision.

HTTP dịch/vision dùng timeout 120 giây mỗi lần gọi (cả request và process worker), tối đa 3 lần thử cho lỗi tạm thời; vẫn có thể hủy khi đang chờ. Request dịch 34 câu của `example1.mp4` đã mất 35,62 giây trong lần kiểm tra provider, vượt giới hạn 30 giây cũ.

### Cấu hình local trên máy phát triển

`config/settings.json` đã nối Whisper small CPU trong `ModelASR/`, VieNeu v3 Turbo ONNX CPU (preset `Hải Đăng`) và HQ3 trong `models/hq3/`. Môi trường ứng dụng `.venv` được cài bằng `python -m pip install -e ".[local]"`; worker dùng `.venv-vieneu` và `.venv-hq3` riêng, đã cài editable SubShort. Chạy từ thư mục `SubShort`:

```powershell
.\.venv\Scripts\python.exe -m subshort.cli
```

VieNeu đang đọc snapshot/cache có sẵn tại `D:/Tools/Projects/DichVideo/TaoGiongDoc/Model` (chỉ đọc, không sao chép model). Đây là cấu hình của máy này, không portable; di chuyển cache/runtime thì sửa đường dẫn trong Cài đặt. HQ3 đã nối nhưng chỉ chạy khi bật giữ âm thanh gốc và tách nền. Job cũ giữ snapshot cũ: thêm job mới để dùng cấu hình mới.

Bản exe đóng gói cả asset VAD của `faster_whisper` (bao gồm `silero_vad_v6.onnx`); đây là dữ liệu bắt buộc của thư viện, không phải model ASR tải riêng. Thiếu asset này làm bước ASR lỗi `NO_SUCHFILE` trước khi gọi API dịch.

Windows GPU: giữ thư mục `cuda/` cạnh `SubShort.exe`. Build spec sao chép cuBLAS 12, cuDNN 9 và NVRTC từ `.venv/Lib/site-packages/nvidia`; entrypoint đăng ký đường dẫn DLL trước khi import ứng dụng. Khi di chuyển bản build, sao chép cả exe và `cuda/`; chỉ sao chép exe sẽ không đủ để chạy Whisper trên GPU. Driver NVIDIA vẫn cần cài trên máy đích.

Pipeline giải mã nguồn video thành `separation_source.wav` stereo PCM16 44.1 kHz bằng FFmpeg trước HQ3; không đưa MP4 trực tiếp cho decoder audio của SDK. File trung gian được giữ trong thư mục job để chẩn đoán.

Kiểm chứng cấu hình này: `output/local-connected-test/acceptance.json` ghi Whisper nhận đúng ba câu mẫu tiếng Anh, VieNeu tạo WAV 48 kHz dài 3.84 giây, HQ3 xuất 169344 frame ở 44.1 kHz và render/ffprobe video thành công. `local-preview.mp4` cùng thư mục dùng câu Việt tự soạn (không phải bản dịch model); đã xem frame phụ đề. Lần kiểm tra lịch sử dừng tại consent dịch (gate này đã được bỏ). Regression `tests/test_pipeline_timing.py` kiểm tra WAV stereo 44.1 kHz tại đầu vào separator. Chưa nghe đánh giá giọng hoặc chất lượng tách nền.

Dịch gửi transcript tới provider đã chọn; Edge TTS gửi bản dịch tới Edge; xóa phụ đề `auto` gửi 3 frame tới provider vision đã chọn. Không có checkbox cho phép gửi riêng; các cờ consent dịch/Edge/vision cũ bị bỏ qua kể cả trong snapshot job. Giấy phép OmniVoice và thao tác upload giữ nguyên.

## Cấu hình và job (Phase 1)

- `source_language` là ngôn ngữ ASR, chuỗi rỗng để tự nhận dạng; `target_language` là ngôn ngữ dịch. Cấu hình cũ thiếu trường nguồn dùng tự nhận dạng, không lấy ngôn ngữ đích làm nguồn.
- `background_volume` giữ đơn vị **0–1** (ví dụ `0.25`); giá trị phần trăm như `25` bị từ chối. Tốc độ phải hữu hạn và dương. Không gộp đoạn nguồn thành clip đọc: mỗi đoạn giữ ID/timestamp riêng; dịch nhiều đoạn cùng request để lấy ngữ cảnh. Cấu hình/job cũ có `merge_words` hoặc `merge_segments` vẫn đọc được nhưng không áp dụng gộp; snapshot mới không giữ hai trường này.
- Hiện có ba mode `subtitle_dub`, `subtitle` và `dub`; ASR `local`/`qwen`/`firered`, dịch `openai`/`gemini`, TTS `edge`/`vieneu`/`omnivoice`/`none`. Mode/provider khác bị chặn thay vì chạy nhánh thay thế âm thầm. FFmpeg/ffprobe và đường dẫn runtime/model của nhánh được chọn phải tồn tại; `separate_background` chỉ yêu cầu model separator khi thật sự trộn nền.
- Job mới lưu snapshot cấu hình ngay khi thêm. Job lịch sử chưa có snapshot lấy cấu hình khi chạy lần đầu nhưng giữ target/mode của job cũ. Retry dùng lại snapshot; đổi settings chỉ áp dụng cho job mới. Snapshot không chứa API key hoặc `extra`; credential hiện tại được lấy khi bắt đầu chạy.
- Job có trạng thái `queued`, `running`, `done`, `failed`, `cancelled`, trường `stage` và `progress` từ 0–100%. Pipeline cập nhật snapshot ở các mốc download/probe/ASR/dịch/TTS/mix/render; hủy giữ stage/progress và intermediate để retry/resume kiểm tra manifest. Retry xóa output/error/progress/metadata cũ.
- Lời Việt bắt đầu theo mốc nguồn chia `slow_speed`, kết thúc trước đoạn kế tiếp (đoạn cuối trước hết video). Clip ngắn giữ nhịp tự nhiên; clip dài tăng tốc giữ cao độ tối đa 1,4×. Nếu vẫn dài, yêu cầu provider rút gọn giữ ý một lần rồi tạo giọng lại; vẫn không vừa thì job lỗi, không cắt tiếng, dời câu sau hoặc kéo hình để bù. Phụ đề theo clip đã xử lý; `final_speed` áp đồng bộ hình/tiếng/phụ đề. Rút gọn có thể phát sinh phí API và cần kiểm tra nội dung.

## Queue, batch và hủy (Phase 2)

- Mỗi lần Start lấy một batch cố định từ job queued/failed, claim trước khi chạy và xử lý tuần tự; bấm Start lặp không tạo worker mới. Job cancelled chỉ chạy lại khi chọn **Thử lại đã chọn**. Retry dùng snapshot job và chỉ reuse intermediate khi manifest khớp source/config/content/model cùng hash artifact; file tồn tại riêng lẻ không đủ.
- **Hủy batch** ngừng nhận job tiếp theo và hủy job hiện tại; giữ intermediate, xóa tham chiếu output để không báo thành công giả. Đóng cửa sổ yêu cầu hủy rồi chờ worker dừng; worker chỉ gửi event, Tk cập nhật trên main thread.
- Queue ghi snapshot dưới khóa vào file tạm, flush/fsync rồi atomic replace. Lỗi ghi giữ file tốt trước đó. JSON/schema hỏng được giữ nguyên và khóa ghi, lỗi hiện trên UI; sửa/khôi phục file rồi mở lại ứng dụng. Running từ lần chạy gián đoạn và done mất output chuyển failed để có thể chạy lại.
- Lỗi từng video chỉ dừng job đó; lỗi xác thực/quota hoặc thiếu dependency provider dừng batch, các job chưa chạy giữ queued. Lỗi lưu queue cũng dừng batch và hiện trên UI.
- Hủy native Whisper chỉ nhận được tại ranh giới model/segment; không cưỡng bức ngắt native inference trong process ứng dụng. Hủy subprocess chỉ kết thúc cây process do job sở hữu; không đụng process khác.
- Khóa queue chỉ bảo vệ các thread trong một instance; không mở hai instance cùng ghi một file lịch sử. Chưa có khóa liên process.

## Credential và request (Phase 3)

- **Cài đặt API** nhập key OpenAI/Gemini/OpenAI-compatible: để trống giữ key hiện tại; chọn **Xóa key** để xóa. Dialog không hiện lại giá trị đã lưu. Các provider dịch dùng contract ID và cần credential tương ứng.
- Credential lưu tại `config/credentials.dpapi`, mã hóa bằng Windows DPAPI theo tài khoản hiện tại; `settings.json` chỉ giữ preference. Không sao chép file này sang tài khoản/máy khác để dùng key. DPAPI không bảo vệ khỏi phần mềm đang chạy dưới cùng tài khoản Windows.
- Key plaintext cũ chỉ được gỡ khỏi JSON sau khi lưu bản bảo vệ thành công. Nếu đọc/migrate thất bại, ứng dụng báo lỗi và giữ dữ liệu cũ; không tự ghi đè credential hỏng. Kiểm tra tài khoản/quyền truy cập hoặc khôi phục bản sao trước khi mở lại. Credential còn trong plaintext legacy cho đến khi migration thành công.
- OpenAI: tối đa 3 lần thử, mỗi lần timeout 30 giây; retry network/timeout/408/429/5xx, backoff 0.5 rồi 1 giây, Retry-After được giới hạn 30 giây. Auth/quota không retry và dừng batch; hủy có hiệu lực cả khi chờ backoff. Thông báo lỗi không chứa response body/key.
- Batch đang chạy giữ credential lúc Start; key sửa trong dialog áp dụng batch sau. Key rỗng bị chặn trước khi gửi request. Không lưu key vào snapshot job.

## ASR và dịch có contract (Phase 4)

- Probe duration/video/audio trước ASR. Nguồn không audio hoặc ASR trả danh sách rỗng được sao chép nguyên byte thành output `*_preserved`, ghi lý do `no_audio`/`no_speech`; không gọi dịch/TTS/render, không áp tốc độ. Đây là nhánh bảo toàn nguồn, không phải phát hiện im lặng bằng ngưỡng năng lượng.
- ASR phải trả text không rỗng và timestamp hữu hạn, không âm, start < end, trong duration nguồn, start/end có thứ tự; cue overlap vẫn được phép. Dữ liệu sai bị chặn trước dịch, không ép kiểu hay bỏ row âm thầm.
- Dịch dùng JSON schema và ID: map theo ID thay vì vị trí, yêu cầu đủ/duy nhất ID và text không rỗng. Response thiếu/trùng/thừa ID gây lỗi; không lấy text nguồn làm fallback. Một request đang chạy tại một thời điểm; tiếp tục dùng timeout/retry/cancel Phase 3. Hủy hoặc response sai không công bố output thành công.

## Timeline và publish (Phase 5)

- Clip TTS được chuẩn hóa mono PCM16 24 kHz và đo bằng số frame thật. Mỗi clip giữ mốc nguồn / `slow_speed`, fit trong cửa sổ bằng tempo tối đa 1,4×; không đẩy start theo clip trước hoặc kéo/freeze hình để che overrun. Raw TTS tách clip fit để resume không áp tempo hai lần.
- `timeline.json` giữ source ID/bounds, cửa sổ, duration raw/fit, tempo và text thực đọc; `pipeline_timing.json` dùng policy `source-windows-v1`. Nhiều speaker dùng cửa sổ từng lane và mix PCM tại mốc nguồn; nhãn/voice map thiếu hoặc chưa duyệt bị chặn.
- `translated_source.srt` giữ timestamp nguồn; caption theo clip cuối / `final_speed`. Không TTS thì chuyển timestamp bằng `slow_speed * final_speed`. Audio và hình cùng áp tốc độ cuối; không dùng `-shortest` để cắt phần dư.
- Video thành phẩm được lưu trực tiếp trong thư mục output đã chọn, tên `<nguồn>_<job-id>_translated.mp4` (hoặc `_preserved` khi giữ nguyên nguồn). Audio, phụ đề, cache, log, video tải về và file render tạm nằm trong thư mục cùng cấp `<output>_artifacts/<job-id>/`; mặc định là `output_artifacts/`. Giữ thư mục artifact để resume và duyệt speaker. Không tự di chuyển các kết quả cũ; job dùng layout cũ khi chạy lại sẽ tạo cache ở layout mới.
- Render vào `*.pending.mp4`, ffprobe kiểm tra video/audio và duration trước khi atomic replace file cuối. Lỗi hoặc hủy xóa file pending, không báo done. Intermediate được giữ để chẩn đoán; file cuối có sẵn từ lần chạy cũ không được xem là kết quả của lần lỗi mới.
- Chọn job rồi mở duyệt transcript/bản dịch/speaker để sửa text theo ID; giữ nguyên timestamp/ID. Lưu sẽ xếp job chạy lại: sửa transcript dịch lại, sửa bản Việt tạo lại giọng phụ thuộc. Bản dịch đầy đủ giữ riêng text rút gọn thực đọc. Không duyệt khi batch đang chạy.
- Bản duyệt transcript/bản dịch/speaker được giữ khi prepare/resume job. Voice map nhiều người có thể dùng UUID giọng custom hoặc ID preset VieNeu do worker trả về (ví dụ `Hải Đăng`, `Trúc Ly`); ID không tồn tại bị chặn. Cue tách lại có ID con riêng để cache/TTS không dùng nhầm bản dịch.
- Diarization cần interpreter và model pyannote đã provision local cùng quyền sử dụng phù hợp; không tự tải model/nhận license. Tích hợp review/voice map/lane đã có, nhưng chưa nghiệm thu nhận diện hai người và nhiều giọng thật trên video mẫu.
- Máy phát triển đã provision Community-1 tại `models/speaker-diarization-community-1`, worker `.venv-diarization/Scripts/python.exe`, `diarization_device="cuda"`. Runtime riêng: pyannote.audio 4.1.0, torch/torchaudio 2.11.0+cu128, torchcodec 0.11.0, pytorch-metric-learning 2.9.0 (`pip check` sạch). Worker tắt telemetry pyannote; caller ép Hugging Face offline. Không bật `multi_speaker` mặc định: người dùng vẫn cần duyệt nhãn và gán voice cho từng speaker.
- Smoke Community-1 offline/CUDA trên 75 giây `opening.wav` đã hoàn tất trong 38,42 giây, exit 0: 29 lượt thuộc `SPEAKER_00`/`SPEAKER_01`, qua validator của ứng dụng. Bằng chứng ở `output/diarization-smoke/` (runtime, JSON kết quả, stderr, digest audio). Nhãn chưa được người nghe đối chiếu nên chưa chứng minh độ chính xác hoặc nghiệm thu lồng nhiều giọng. Runtime còn cảnh báo decoder torchcodec thiếu DLL; worker hiện đọc WAV PCM bằng `wave` và truyền tensor trực tiếp, nhánh này đã chạy thành công, không phụ thuộc decoder đó.
- Kiểm thử routing nhiều giọng thật ở `output/multivoice-context75-20261118/`: nguồn 75 giây, diarization mới trả hai nhãn; 30 clip VieNeu CUDA dùng `Hải Đăng` (8 clip) và `Trúc Ly` (22 clip), mix và render thành video 187,5 giây. ASR/bản dịch dùng cache thật; duyệt kỹ thuật bằng overlap lớn nhất theo cue, **không phải người nghe xác nhận nhãn**. `slow_speed=0.4` chỉ phục vụ scenario fit, không phải cấu hình chất lượng khuyến nghị. `acceptance.json`, `run-provenance.json`, `timeline.json` giữ bằng chứng; chưa nghiệm thu nội dung dịch, giới tính, độ tự nhiên hoặc lip-sync.

## Tính năng

- Video local hoặc URL qua yt-dlp.
- ASR local Whisper, Qwen hoặc FireRed qua `ASRSession`; model ASR/aligner là thư mục, model VAD là tệp model tồn tại cục bộ.
- CUDA trên Windows cần runtime trong đúng interpreter, không chỉ đặt `asr_device="cuda"`. Whisper/CTranslate2 dùng `python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` trong môi trường chạy ứng dụng. Qwen/FireRed dùng torch và torchaudio cùng bản CUDA; RTX 5060 Ti (Blackwell) cần bản có hỗ trợ kiến trúc này, ví dụ wheel CUDA 12.8 từ `https://download.pytorch.org/whl/cu128`. Không dùng DLL trong thư mục DichVideo làm dependency ngầm. Xác nhận bằng inference audio thật; `torch.cuda.is_available()` hoặc cờ cấu hình không đủ chứng minh model chạy GPU.
- Dịch qua OpenAI/Gemini.
- Phụ đề SRT/ASS.
- TTS Edge hoặc session local VieNeu/OmniVoice; voice preset/custom được lưu trong settings.
- Trộn audio, giữ nhạc nền, tách nền tùy chọn qua worker riêng.
- Queue persistent, resume, cancel, output profile và lịch sử.
- Cấu hình API được bảo vệ bằng Windows DPAPI; không commit credential hay khóa thật.
- Làm mờ phụ đề cũ: Cài đặt → Phụ đề cũ, mặc định `off`. `manual` nhận JSON `[x,y,w,h]` chuẩn hóa 0–1 theo nguồn; `auto` dùng đúng provider/model vision đã chọn và lấy/gửi 3 frame. Gemini cần model hỗ trợ ảnh của Gemini; không tự đổi model/provider. Lưu frame PNG và `subtitle_region.json` trong thư mục job; đây là ảnh nguồn cục bộ cần bảo vệ/xóa khi không cần. Box sai hoặc không có biên pixel làm job lỗi, không đoán vùng đáy; kết quả không có phụ đề không blur. Refinement dùng biên tương phản, không OCR/inpaint; cần xem diagnostic/output trước khi tin cậy trên footage thật. Blur áp theo nguồn trước mọi layout.
- ASS dùng Encoding=-1 của libass để giữ thứ tự Arabic/Hebrew qua các token karaoke; đã xem render RTL, mixed Latin/numbers và multiline. Không bảo đảm cùng cách hiển thị trong VSFilter hoặc mọi trình phát ASS ngoài.
- Upload Google Drive tùy chọn qua adapter, credentials không đi kèm project.

## Build Windows

```powershell
.\.venv\Scripts\python -m pip install pyinstaller
.\.venv\Scripts\pyinstaller packaging/subshort.spec
```

Bản build một file nằm ở `dist/SubShort.exe`. FFmpeg/ffprobe không được bundle: cài vào `PATH` hoặc cấu hình đường dẫn tuyệt đối. Khi dùng interpreter ngoài cho TTS/separation, môi trường đó phải cài cả SubShort lẫn SDK tương ứng để import được worker `subshort.*`.

Worker TTS ngoài exe được ép stdin/stdout/stderr UTF-8 qua `PYTHONIOENCODING`, tránh làm hỏng tên giọng và nội dung tiếng Việt khi Windows mặc định dùng code page khác.

Mỗi job lưu `diagnostics.log` UTF-8 trong thư mục artifact: provider/model, kết quả kiểm tra response và lý do lỗi JSON/schema/ID/text. Rút gọn ghi số clip, ID nguồn, thời lượng audio và cửa sổ fit; không ghi key, transcript hay response body. `OK` chỉ xuất hiện sau validation nội dung thành công.

Windows chạy FFmpeg/ffprobe và worker nền với `CREATE_NO_WINDOW`, kể cả lệnh hủy cây tiến trình. Prompt dịch/rút gọn luôn nêu rõ JSON object `items`; response sai vẫn bị từ chối với lý do riêng cho root type, thiếu/thừa trường và kiểu `items`.

Executable hiện tại đã chạy qua UI thật ngoài checkout tại đường dẫn Unicode/khoảng trắng: enqueue video tự tạo không audio, hoàn tất `done: no_audio`, bảo toàn byte và mở lại giữ queue/output. Proof log/ảnh ở `output/P10 UI acceptance/`. Đây là máy phát triển, không phải Windows sạch; chưa chứng minh pipeline đầy đủ qua binary.

Model ASR local, tách nền, CUDA và Google Drive là adapter tùy chọn; ứng dụng không tải model âm thầm. Cấu hình provider và model phải được cung cấp trước khi chạy pipeline tương ứng.

ASR và TTS local chỉ dùng model/cache đã có trên máy và worker TTS chạy offline. Session được reuse trong một batch, hủy sẽ dọn worker do job sở hữu; khi CUDA không khả dụng, ứng dụng chỉ fallback sang CPU với **cùng model** và phát diagnostic, không tự đổi provider/model. VieNeu v3 Turbo ONNX luôn chạy CPU và báo diagnostic nếu chọn CUDA. Đã có smoke Whisper CPU với speech/word/reuse/fallback, Whisper CUDA và NVENC hardware; encoder CPU fallback cũng đã render/ffprobe/validate thật. Whisper CUDA cần cuBLAS CUDA 12/cuDNN tương thích trên `PATH` trước khi chạy; lần smoke dùng runtime cục bộ chỉ đọc, không chứng minh bản cài mặc định hoặc Windows sạch đã có runtime. Render ASS tiếng Việt đã được xem frame ở ba layout; VieNeu custom reference đã qua registry mở lại và inference offline. Chưa nghe A/B, đánh giá độ giống giọng, chất lượng mọi model hoặc nghiệm thu vision cloud; smoke không thay thế các gate đó.
Smoke ASR tiếng Trung AISHELL-3 offline đã chạy qua `ASRSession` CPU riêng với WAV ghim SHA-256 `e5f8e2417a89ce2418c59d419be999b46206890d1f094b76f77ad4d41b365bca`: Qwen3-ASR-0.6B và FireRed AED-L trả exact transcript, CER 0%; Qwen có 11 đoạn alignment hợp lệ trong 4.966 giây. Whisper small có transcript gần đúng nhưng CER 36.36%. Đây là bằng chứng provider/model độc lập, không phải pipeline production tiếng Trung hay nghiệm thu chất lượng; chi tiết command/runtime/license ở `output/asr-chinese-acceptance/`.
