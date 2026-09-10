# AutoCAD PID Persistence Research

> Ngày lập: 2026-09-10
> Loại: nghiên cứu / so sánh / tiền nghiệm thu N2
> Phạm vi: persistent document/entity identity cho CDT-AutoCAD trên AutoCAD 2027 Managed .NET
> Nguồn: Autodesk AutoCAD 2027 Managed/ObjectARX documentation

## 1. Executive Summary

- Candidate ưu tiên cho `document_pid`: application-owned `DBDictionary` + `Xrecord` dưới Named Objects Dictionary (NOD).
- Candidate ưu tiên cho `entity semantic_pid`: entity Extension Dictionary + application-owned `Xrecord`.
- Lý do: dictionary/XRecord là native DWG database objects; extension dictionary là hard-owned bởi object và deep-clone mặc định clone các owned objects, nên đây là candidate phù hợp để đo clone semantics thay vì suy đoán.
- `ObjectId` chỉ dùng runtime/native access; Handle hỗ trợ native lookup nhưng không phải semantic PID. Clone có thể tạo ObjectId/Handle mới và `IdMapping` cung cấp map source->clone.
- N2 không được chốt carrier chỉ từ tài liệu. Bắt buộc prototype thật trên AutoCAD 2027 cho save/reopen, edit, same-database clone, cross-database WblockClone, erase/unerase/undo/redo và duplicate-PID behavior.

## 2. Phân tích

### 2.1 Native storage candidates

Autodesk mô tả AutoCAD `Database` chứa graphical objects, symbol tables và named dictionaries. Dictionary có thể chứa AutoCAD objects hoặc `XRecord`; dictionary có thể nằm dưới Named Objects Dictionary hoặc extension dictionary của table record/entity.

Nguồn:
- https://help.autodesk.com/cloudhelp/2027/HUN/OARX-DevGuide-Managed/files/GUID-7313ECA1-4875-4946-82E3-C06A4074F807.htm

`Xrecord` là built-in database storage object, có thể chứa dữ liệu lớn và có thể được owned bởi extension dictionary của một object. Autodesk cũng dùng extension-dictionary/XRecord pattern nội bộ cho layer states, nên pattern này là native/common AutoCAD storage idiom.

Nguồn:
- https://help.autodesk.com/cloudhelp/2027/ENU/OARX-RefGuide/files/OARX-RefGuide-AcDbXrecord.html
- https://help.autodesk.com/cloudhelp/2027/ESP/OARX-DevGuide-Managed/files/GUID-80B3DAD6-B1F6-4BCC-86C7-7FA2444EEDFB.htm

Autodesk khuyến cáo dùng XDATA/extension dictionaries có kiểm soát; khi cần nhiều hơn XDATA thì cân nhắc XRecord trong extension dictionary. PID payload của CDT rất nhỏ, nhưng extension dictionary/XRecord có lợi thế ownership rõ ràng và không cần RegisteredApplication XDATA surface.

Nguồn:
- https://help.autodesk.com/cloudhelp/2021/ENU/OARX-IOP/files/GUID-752D0BE7-CF03-4AC1-8AA6-2DE5BE6A054A.htm

### 2.2 Clone semantics

Autodesk mô tả default `DeepClone` là clone primary object rồi clone các owned objects bằng deep-clone filer. Extension dictionary là hard-owned bởi object; extension dictionary entries là hard-owned. Điều này làm cho entity-extension-dictionary/XRecord trở thành candidate mạnh cho việc quan sát PID duplication khi entity được copied/cloned.

Nguồn:
- https://help.autodesk.com/cloudhelp/2024/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_DBObject_DeepClone_DBObject_IdMapping__MarshalAsUnmanagedType_U1__bool.html
- https://help.autodesk.com/cloudhelp/2019/JPN/OARXMAC-DevGuide/files/GUID-55F1BA17-AB2C-4C05-810A-D5826CDF93E9.htm

Cross-database cloning dùng `Database.WblockCloneObjects`; `IdMapping` chứa source/new ObjectIds. Vì PID metadata có thể được cloned cùng ownership graph, CDT phải có explicit clone policy: conceptual copy phải nhận PID mới; clone operation phải được phát hiện/remap, không được để hai objects cùng semantic PID đi qua validation.

Nguồn:
- https://help.autodesk.com/view/ACD/2027/ENU/?caas=caas%2Fdocumentation%2FACD%2F2014%2FENU%2Ffiles%2FGUID-E02A8AAF-61FF-4C72-8960-0AEEBBEC2594-htm.html
- https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_IdMapping.html

### 2.3 Transaction / rollback relevance

AutoCAD Managed .NET `TransactionManager.StartTransaction()` provides explicit commit/rollback boundaries. N2 storage write/read must therefore occur inside native transactions; future N5 executor can reuse the same PID storage primitives under the full Semantic State Loop.

Nguồn:
- https://help.autodesk.com/view/ACD/2027/ENU/?caas=caas%2Fdocumentation%2FACD%2F2014%2FENU%2Ffiles%2FGUID-50FD6118-B2D1-4313-A7D6-830794DFDEFA-htm.html

### 2.4 Events relevant to identity lifecycle

Managed .NET object/database events include copied, erased, modified, reappended and unappended notifications. These can later assist PID lifecycle handling and semantic delta capture, but N2 treats post-operation database reads as ground truth rather than trusting event delivery alone.

Nguồn:
- https://help.autodesk.com/view/ACD/2027/ENU/?caas=caas%2Fdocumentation%2FACD%2F2014%2FENU%2Ffiles%2FGUID-E30279D1-E4B5-48A4-A3D8-9CEC83BD0967-htm.html

## 3. So sánh candidate

| Candidate | Ưu điểm | Nhược điểm / rủi ro | N2 decision |
|---|---|---|---|
| XDATA | Nhẹ, phổ biến, persisted | 16KB/object shared surface; RegisteredApplication; clone semantics vẫn phải xử lý | Không ưu tiên |
| Entity Extension Dictionary + XRecord | Native ownership, persisted, built-in, phù hợp semantic metadata | Clone có khả năng copy PID cùng entity; overhead cao hơn XDATA; cần clone policy | **Prototype entity PID** |
| NOD Dictionary + XRecord | Database-level native metadata; phù hợp document identity | WBLOCK/INSERT semantics phải đo; duplicate named entry handling cần policy | **Prototype document PID** |
| Handle only | Native, persisted trong cùng drawing | Clone/import/copy tạo identity mới; không mô tả conceptual identity | Lookup aid only |
| ObjectId only | Nhanh trong process/database | Runtime/session identity, không phải persistent semantic identity | Runtime only |

## 4. Khuyến nghị / ADR refinement

N2 prototype dùng namespace key riêng, không dùng prefix `ACAD_` vì prefix này thuộc AutoCAD. Candidate key:

- NOD dictionary: `SLNCTRZ_CDT`
- document XRecord: `DOCUMENT_PID`
- entity extension-dictionary XRecord: `SLNCTRZ_CDT_PID`
- payload schema: version + PID string; không lưu fingerprint vào PID record ở N2 để tách identity khỏi content proof.

Clone policy cần thử nghiệm trước khi chốt:

1. Original entity giữ PID.
2. Conceptual copy phải nhận PID mới.
3. Nếu native clone mang PID cũ sang clone, post-clone reconciliation phải phát hiện duplicate và remap clone qua `IdMapping`/clone event context hoặc deterministic post-pass.
4. Cross-document clone tuyệt đối không được silently alias semantic PID.
5. Document PID của destination phải thuộc destination database, không bị source NOD metadata overwrite.

## 5. N2 acceptance matrix — live result

Tất cả probe chạy trong AutoCAD 2027 thật và PASS:

- P0 create document PID + entity PID — PASS.
- P1 save/cold-reopen persistence — PASS.
- P2 ordinary edit preserves entity PID — PASS.
- P3 same-database `DeepCloneObjects` — PASS; raw clone mang PID cũ, bắt buộc remap.
- P3b shallow `DBObject.Clone()` — PASS; extension dictionary/PID không đi theo clone, phải cấp PID mới.
- P4 cross-database `WblockCloneObjects` — PASS; raw clone mang PID cũ, destination document PID độc lập, remap persisted.
- P5 erase/unerase — PASS; PID preserved/readable.
- P6 real AutoCAD undo/redo — PASS; PID/document identity stable.
- P7 deliberate duplicate PID corruption — PASS; exact collision detected and repaired.
- P8 native transaction abort — PASS; geometry, appended entity và existing PID XRecord overwrite đều rollback.
- P9 WBLOCK/INSERT/block definition/reference — PASS; entity PID được clone qua WBLOCK/INSERT, document PID được re-identify; BlockTableRecord/content/BlockReference có PID riêng và cold-reopen unique.
- P10 raw filesystem copy — PASS; byte-identical copy giữ nguyên document PID và toàn bộ entity PID, chứng minh document PID là semantic lineage identity chứ không phải physical-file UUID.

Evidence canonical: `docs/evidence/n2-pid-native-2026-09-10.json`; acceptance summary: `docs/N2_PID_ACCEPTANCE.md`.

## 6. Kết luận

Ngày chốt research: 2026-09-10.

Sau native P0-P10, **carrier decision được chốt cho kiến trúc N3+**: NOD XRecord cho `document_pid` và Extension Dictionary XRecord cho managed DBObject PID. Carrier chỉ hợp lệ khi đi cùng clone reconciliation policy: conceptual copy luôn nhận PID mới; deep/cross/WBLOCK/INSERT phải remap trước acceptance; duplicate PID là blocking integrity defect. `ObjectId`/Handle vẫn chỉ là native lookup aids. P10 bổ sung invariant: mutation target phải bind bằng runtime document context + `document_pid` + `expected_parent_fp`; artifact/checkpoint identity thêm `artifact_fp`, và duplicate open lineage PID phải fail closed tới khi disambiguate.

Kết quả quan trọng nhất: AutoCAD 2027 thực sự clone Extension-Dictionary PID qua `DeepCloneObjects`, `WblockCloneObjects`, WBLOCK và INSERT trong các path đã đo. Vì vậy không được dựa vào persistence carrier một mình để định danh — **PID persistence + clone policy + duplicate scan** là một invariant thống nhất.

## 7. Render HTML

Project/gateway hiện không có `scripts/md2html.ps1` hoặc `docs/report-components.md`; đã kiểm tra cả gateway workspace và Windows H: drive. Vì renderer chuẩn không hiện diện, HTML research artifact chưa được sinh trong N2 research checkpoint này. Không tự tạo renderer thay thế để tránh biến đổi toolchain ngoài phạm vi.
