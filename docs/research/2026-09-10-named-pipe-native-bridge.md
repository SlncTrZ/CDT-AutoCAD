# AutoCAD Native Bridge IPC Research — N3

> Ngày lập: 2026-09-10
> Loại: nghiên cứu / kiến trúc / N3 acceptance record
> Phạm vi: local IPC, AutoCAD-safe dispatch, document binding, secure deployment và read-only bridge skeleton
> Nguồn: Microsoft .NET 10 Named Pipes documentation; Autodesk AutoCAD 2027 Managed .NET Developer Guide/Reference

## 1. Executive Summary

- N3 dùng **Windows Named Pipe**, không dùng localhost HTTP/TCP. Final accepted boundary dùng `PipeOptions.CurrentUserOnly` cộng explicit local-computer check và same-Windows-session check; same-user Session 0 bị từ chối.
- Wire protocol là **length-prefixed UTF-8 JSON**, versioned và bounded; không dùng newline framing, không nhận arbitrary C#/LISP/AutoCAD command text.
- Pipe accept/read chạy background thread, nhưng **không thread nền nào được gọi AutoCAD API**. Request được queue và xử lý từ AutoCAD `Application.Idle` callback; đây là N3 dispatcher ban đầu, read-only.
- Runtime document identity là bridge-owned `runtime_document_id` theo vòng đời `Document` trong một `bridge_instance_id`. `document_pid` của N2 chỉ là semantic-lineage identity; mutation về sau bắt buộc bind `runtime_document_id + document_pid + expected_parent_fp`.
- N3 milestone đầu chỉ expose nội bộ `bridge.health`, `bridge.documents.list`, `bridge.document.identity`. **Không có mutation endpoint.** Parent-state fingerprint validation chỉ được bật khi N4/N6 semantic extraction/fingerprint integration tồn tại.

## 2. Ground truth

### 2.1 AutoCAD 2027 Managed .NET runtime

Autodesk xác nhận AutoCAD 2027 (release 26.0) dùng AutoCAD 2027 Managed .NET SDK và .NET 10.0. Production bridge vì vậy target `net10.0-windows` và chạy in-process trong `acad.exe`.

Nguồn:
- https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-Customization/files/GUID-A6C680F2-DE2E-418A-A182-E4884073338A.htm
- https://help.autodesk.com/cloudhelp/2027/CSY/OARX-DevGuide-Managed/files/GUID-C8C65D7A-EC3A-42D8-BF02-4B13C2EA1A4B.htm

### 2.2 Named Pipe same-user boundary

Microsoft .NET 10 documents `PipeOptions.CurrentUserOnly` as a current-user-only pipe boundary. For the ACL factory path, if this option is present, a custom security descriptor is created with the current Windows user as owner and full-control principal; supplied custom `PipeSecurity` is ignored.

N3 therefore uses the built-in current-user option rather than combining it with a second custom ACL model. This keeps the first bridge small and removes SID/account-name lookup complexity.

Nguồn:
- https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstreamacl.create?view=net-10.0
- https://learn.microsoft.com/en-us/dotnet/standard/io/how-to-use-named-pipes-for-network-interprocess-communication

### 2.3 AutoCAD-safe execution context

Autodesk exposes `DocumentCollection.ExecuteInCommandContextAsync`, but command-context queuing is unnecessary for the first read-only N3 lane. AutoCAD also exposes the application `Idle` event; handlers run when AutoCAD is idle and can be registered for the application lifetime.

N3 chooses a background pipe transport plus an AutoCAD `Application.Idle` dispatcher. This gives a simple invariant: background I/O owns no `Document`, `Database`, `Transaction`, `ObjectId` or other AutoCAD object. Native reads happen only when the queued request is drained inside AutoCAD's event callback.

Nguồn:
- https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-__MEMBERTYPE_Methods_Autodesk_AutoCAD_ApplicationServices_DocumentCollection.html
- https://help.autodesk.com/cloudhelp/2027/CHT/OARX-DevGuide-Managed/files/GUID-4BD5D384-5448-4D19-9023-DA12A55FAEF0.htm
- https://help.autodesk.com/cloudhelp/2027/RUS/OARX-DevGuide-Managed/files/GUID-E619BB54-D531-4640-BB74-B61E6CA13238.htm

Future write operations still require explicit document-lock/transaction design; N3 read-only completion is not authorization to mutate.

### 2.4 Secure plugin deployment

Autodesk recommends executable code be kept in trusted locations with `SECURELOAD` enabled and `.bundle` is the standard AutoCAD plug-in package mechanism. Documentation describes ProgramData and user-profile ApplicationPlugins options, but measured AutoCAD 2027 behavior on `.171` did not enumerate the ProgramData bundle during `APPAUTOLOADER Reload`. The acceptance deployment therefore uses the per-user `%APPDATA%\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle` and explicitly trusts only its `Contents\Windows` directory while preserving `SECURELOAD=1`.

This measured machine behavior outranks a generic deployment assumption for the certification lane. The source remains in-repo; only the built DLL + package manifest are copied into the dedicated trusted bundle.

Nguồn:
- https://help.autodesk.com/view/ACD/2027/ENU/?caas=caas/documentation/ACD/2014/ENU/files/GUID-2FB4611D-F141-48D5-9B6E-460EB59351AF-htm.html
- https://help.autodesk.com/view/ACD/2027/ENU/?caas=caas/documentation/ACD/2014/ENU/files/GUID-5E50A846-C80B-4FFD-8DD3-C20B22098008-htm.html

## 3. Wire protocol decision

Protocol ID: `cdt-autocad-native-v1`.

Framing:

```text
uint32 little-endian payload_length
UTF-8 JSON payload[payload_length]
```

Limits for N3 read-only lane:

- maximum payload: 65,536 bytes;
- exact 4-byte header required;
- invalid/zero/oversized length rejected before payload allocation;
- UTF-8 must be valid;
- JSON must be an object;
- request envelope rejects unknown top-level fields;
- canonical UUID `request_id` required;
- server response carries the same request ID when one was safely parsed;
- unsupported operation is a typed refusal, not an exception leak.

Initial operation allowlist:

```text
bridge.health
bridge.documents.list
bridge.document.identity
```

There is deliberately no `execute`, `command`, `script`, `lisp`, `csharp`, `shell`, or generic mutation operation.

## 4. Runtime document binding

N2 proved raw DWG copies can carry the same `document_pid`. Therefore `document_pid` cannot uniquely select a live document.

N3 introduces:

- `bridge_instance_id`: random UUID per loaded bridge instance;
- `runtime_document_id`: random UUID associated with one AutoCAD `Document` object for the lifetime of that bridge instance;
- `document_pid`: persistent semantic-lineage PID read from native XRecord; may be absent for an unmanaged drawing;
- path/name: descriptive evidence, not authoritative identity.

Read-only `bridge.document.identity` requires `runtime_document_id`. A caller may also supply `document_pid` as an assertion; mismatch fails closed.

Future mutation binding is reserved as:

```text
bridge_instance_id
+ runtime_document_id
+ document_pid
+ expected_parent_fp
```

`expected_parent_fp` cannot be truthfully validated in N3 because authoritative native SemanticSnapshot/fingerprint extraction is N4/N6 work. N3 must not fake this gate.

If multiple open documents share the same lineage PID, `bridge.documents.list` may report both with distinct runtime IDs. PID-only mutation resolution is prohibited.

## 5. Dispatcher model

```text
Python / provider
      |
      v
Named Pipe background I/O
      |
      v
bounded PendingRequest queue
      |
      v
AutoCAD Application.Idle
      |
      v
ReadOnlyDispatcher
      |
      +--> DocumentRegistry
      +--> PID XRecord read
      +--> health/version/document data
      |
      v
TaskCompletionSource response
      |
      v
pipe response frame
```

Rules:

- pipe thread never touches AutoCAD API;
- queue is bounded;
- request execution timeout is bounded;
- one N3 request is dispatched at a time initially;
- shutdown cancels pending requests and stops accepting clients;
- response errors contain stable codes and bounded messages only.

## 6. Pipe naming

Initial single-host-per-user-session name:

```text
SlncTrZ.CDT.AutoCAD.Bridge.v1.s<WindowsSessionId>
```

`CurrentUserOnly` separates Windows users; session suffix avoids collision between sessions of the same user. Multiple AutoCAD processes in one user/session are intentionally not supported in N3. The bridge reserves its first pipe instance synchronously during `IExtensionApplication.Initialize`, so a normally detected already-owned same-session endpoint fails plugin startup instead of entering a silent background retry. N3 does not claim a multi-process discovery/multiplexing contract; that can be designed later if a real requirement appears.

## 7. Build configuration finding

The machine has AutoCAD 2027 product managed assemblies and user-local .NET SDK 10.0.401. No installed AutoCAD/ObjectARX Managed SDK project templates were found in the scanned Autodesk roots.

The N2 probe builds against product `AcCoreMgd.dll`, `AcDbMgd.dll`, and `AcMgd.dll` with `Private=false` but emits three MSB3277 warning families (`Microsoft.VisualBasic`, `System.Drawing`, `WindowsBase`). Autodesk confirms .NET 10 is correct and recommends AutoCAD references with Copy Local disabled, but the installed product-reference warning set remains unresolved.

N3 therefore carries this as an explicit NB0 build issue: do not suppress warnings with `NoWarn`. Prefer AutoCAD 2027 Managed/ObjectARX SDK reference assemblies when available; until then native runtime acceptance is evidence, not proof that the reference conflict is harmless.

Nguồn:
- https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-Customization/files/GUID-A6C680F2-DE2E-418A-A182-E4884073338A.htm
- https://help.autodesk.com/cloudhelp/2027/CSY/OARX-DevGuide-Managed/files/GUID-43564EB9-F843-4771-823C-573495EE23E0.htm

## 8. So sánh phương án IPC

| Phương án | Ưu điểm | Nhược điểm | Quyết định |
|---|---|---|---|
| Named Pipe + CurrentUserOnly | local native Windows IPC; same-user boundary; no port/firewall; duplex | Windows-specific; multi-process discovery cần thiết kế nếu mở rộng | **Chọn cho N3** |
| localhost HTTP | tooling dễ; debug đơn giản | mở TCP surface; firewall/port/auth lifecycle; dư thừa cho same-host bridge | Không chọn |
| COM-only | hiện có, tương thích bootstrap | out-of-process, không phải native transaction/semantic bridge | Giữ fallback/migration |
| Memory-mapped/shared memory | nhanh | synchronization/protocol/security phức tạp, không cần cho control plane | Không chọn |

## 9. N3 acceptance checkpoint đầu

Trước khi có mutation, phải chứng minh:

1. plugin load/unload và protocol identity;
2. pipe dùng `CurrentUserOnly`, không TCP listener;
3. length limit/invalid UTF-8/malformed JSON/bad request ID/unsupported op fail typed và server sống tiếp;
4. request correlation round-trip and concurrent client requests are serialized safely;
5. `bridge.health` chạy từ AutoCAD process và báo `mutation_enabled=false`;
6. `bridge.documents.list` trả distinct `runtime_document_id`;
7. `bridge.document.identity` bắt buộc runtime ID và fail nếu asserted lineage PID mismatch;
8. hai document có cùng `document_pid` vẫn được phân biệt bởi runtime ID; PID-only targeting không tồn tại;
9. read-only calls không dirty drawing;
10. public 50-tool MCP surface không thay đổi.

## 10. Khuyến nghị / ADR

Chốt N3.0 theo mô hình **Named Pipe current-user-only + bounded typed protocol + Idle-thread native dispatcher + runtime document registry**.

Không thêm mutation endpoint trong N3. Read-only transport/document binding đã PASS trên AutoCAD 2027; bước tiếp theo là N4 native semantic extraction, vẫn read-only.

## 11. Kết luận

Ngày chốt: 2026-09-10.

N3 read-only bridge skeleton đã được triển khai và live-verified trên AutoCAD 2027. Final acceptance dùng bundle per-user + explicit trusted contents path, protocol `cdt-autocad-native-v1`, same-user/local/same-session Named Pipe boundary và `Application.Idle` native dispatcher. Hai trụ cột được giữ ngay từ transport boundary: document targeting không dựa vào lineage PID đơn độc, và mọi future mutation field đã được dành chỗ cho `expected_parent_fp` nhưng chưa giả vờ validate khi semantic extractor chưa tồn tại. Canonical measured evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

## 12. Render HTML

Đã kiểm tra workspace và `/mnt/pc-dev` nhưng không có `scripts/md2html.ps1` hoặc `docs/report-components.md`. Vì renderer chuẩn của harness không hiện diện, không tự chế renderer thay thế; HTML research artifact để `BLOCKED_BY_TOOLCHAIN` cho tới khi renderer chuẩn được cung cấp.
