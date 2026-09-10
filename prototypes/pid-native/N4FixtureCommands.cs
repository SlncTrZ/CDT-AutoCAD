// N4FixtureCommands — disposable rich semantic fixture for native read-only acceptance.
// Wing: ops | Topic: native-bridge-n4 | Updated: 2026-09-10 13:20

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: CommandClass(typeof(CDT.AutoCAD.PidProbe.N4FixtureCommands))]

namespace CDT.AutoCAD.PidProbe;

public static class N4FixtureCommands
{
    private static ObjectId _definitionLineId = ObjectId.Null;

    private static string FixturePath =>
        Path.Combine(ProbeReport.ReportDirectory, "n4-semantic-fixture.dwg");

    [CommandMethod("CDT_N4_CREATE_FIXTURE", CommandFlags.Modal)]
    public static void CreateFixture()
    {
        Document document = AcApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document");
        Database database = document.Database;
        Dictionary<string, object> evidence = new(StringComparer.Ordinal);
        string documentPid;

        using (Transaction transaction = database.TransactionManager.StartTransaction())
        {
            documentPid = PidStorage.GetOrCreateDocumentPid(database, transaction);
            BlockTable blockTable = (BlockTable)transaction.GetObject(
                database.BlockTableId,
                OpenMode.ForRead
            );
            BlockTableRecord model = (BlockTableRecord)transaction.GetObject(
                blockTable[BlockTableRecord.ModelSpace],
                OpenMode.ForWrite
            );

            Line line = new(new Point3d(0, 0, 0), new Point3d(10, 0, 0));
            Add(model, line, "line", evidence, transaction);

            Circle circle = new(new Point3d(20, 5, 0), Vector3d.ZAxis, 3);
            Add(model, circle, "circle", evidence, transaction);

            Arc arc = new(new Point3d(30, 5, 0), 4, 0, Math.PI / 2);
            Add(model, arc, "arc", evidence, transaction);

            Polyline polyline = new();
            polyline.AddVertexAt(0, new Point2d(0, 10), 0, 0, 0);
            polyline.AddVertexAt(1, new Point2d(10, 10), 0, 0, 0);
            polyline.AddVertexAt(2, new Point2d(10, 15), 0, 0, 0);
            polyline.AddVertexAt(3, new Point2d(0, 15), 0, 0, 0);
            polyline.Closed = true;
            Add(model, polyline, "lwpolyline", evidence, transaction);

            DBText text = new()
            {
                Position = new Point3d(15, 12, 0),
                Height = 1.5,
                TextString = "CDT N4 TEXT",
            };
            Add(model, text, "text", evidence, transaction);

            MText mtext = new()
            {
                Location = new Point3d(15, 16, 0),
                TextHeight = 1.25,
                Contents = "CDT N4 MTEXT",
            };
            Add(model, mtext, "mtext", evidence, transaction);

            blockTable.UpgradeOpen();
            string blockName = $"CDT_N4_BLOCK_{Guid.NewGuid():N}";
            BlockTableRecord definition = new() { Name = blockName };
            ObjectId definitionId = blockTable.Add(definition);
            transaction.AddNewlyCreatedDBObject(definition, true);
            string definitionPid = PidStorage.GetOrCreateEntityPid(definition, transaction);
            evidence["block_definition"] = new
            {
                pid = definitionPid,
                handle = definition.Handle.ToString(),
            };
            Line blockLine = new(new Point3d(0, 0, 0), new Point3d(2, 2, 0));
            _definitionLineId = definition.AppendEntity(blockLine);
            transaction.AddNewlyCreatedDBObject(blockLine, true);
            string blockLinePid = PidStorage.GetOrCreateEntityPid(blockLine, transaction);
            evidence["block_definition_line"] = new
            {
                pid = blockLinePid,
                handle = blockLine.Handle.ToString(),
            };
            BlockReference insert = new(new Point3d(25, 15, 0), definitionId);
            Add(model, insert, "insert", evidence, transaction);

            AlignedDimension dimension = new(
                new Point3d(0, 20, 0),
                new Point3d(10, 20, 0),
                new Point3d(5, 22, 0),
                string.Empty,
                database.Dimstyle
            );
            Add(model, dimension, "dimension", evidence, transaction);

            Polyline hatchBoundary = new();
            hatchBoundary.AddVertexAt(0, new Point2d(20, 20), 0, 0, 0);
            hatchBoundary.AddVertexAt(1, new Point2d(28, 20), 0, 0, 0);
            hatchBoundary.AddVertexAt(2, new Point2d(28, 26), 0, 0, 0);
            hatchBoundary.AddVertexAt(3, new Point2d(20, 26), 0, 0, 0);
            hatchBoundary.Closed = true;
            ObjectId boundaryId = Add(model, hatchBoundary, "hatch_boundary", evidence, transaction);

            Hatch hatch = new();
            model.AppendEntity(hatch);
            transaction.AddNewlyCreatedDBObject(hatch, true);
            hatch.SetHatchPattern(HatchPatternType.PreDefined, "SOLID");
            hatch.Associative = true;
            hatch.AppendLoop(HatchLoopTypes.Default, new ObjectIdCollection([boundaryId]));
            hatch.EvaluateHatch(true);
            string hatchPid = PidStorage.GetOrCreateEntityPid(hatch, transaction);
            evidence["hatch"] = new { pid = hatchPid, handle = hatch.Handle.ToString() };

            transaction.Commit();
        }

        database.SaveAs(FixturePath, DwgVersion.Current);
        string report = ProbeReport.Write(
            "n4-semantic-fixture",
            new
            {
                status = "PASS",
                document_pid = documentPid,
                fixture_path = FixturePath,
                entities = evidence,
            }
        );
        document.Editor.WriteMessage($"\nCDT N4 fixture: {report}");
    }

    [CommandMethod("CDT_N4_CREATE_OVERSIZE_ENTITY", CommandFlags.Modal)]
    public static void CreateOversizeEntity()
    {
        Document document = AcApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document");
        Database database = document.Database;
        using Transaction transaction = database.TransactionManager.StartTransaction();
        BlockTable blockTable = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead
        );
        BlockTableRecord model = (BlockTableRecord)transaction.GetObject(
            blockTable[BlockTableRecord.ModelSpace],
            OpenMode.ForWrite
        );
        MText oversized = new()
        {
            Location = new Point3d(50, 50, 0),
            TextHeight = 1.0,
            Contents = new string('X', 80_000),
        };
        model.AppendEntity(oversized);
        transaction.AddNewlyCreatedDBObject(oversized, true);
        _ = PidStorage.GetOrCreateEntityPid(oversized, transaction);
        transaction.Commit();
        document.Editor.WriteMessage("\nCDT N4 oversized semantic entity created");
    }

    [CommandMethod("CDT_N4_MUTATE_BLOCK_DEFINITION", CommandFlags.Modal)]
    public static void MutateBlockDefinition()
    {
        Document document = AcApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document");
        if (_definitionLineId.IsNull || !_definitionLineId.IsValid)
        {
            throw new InvalidOperationException("N4 block definition fixture is not initialized");
        }

        using Transaction transaction = document.Database.TransactionManager.StartTransaction();
        Line line = (Line)transaction.GetObject(_definitionLineId, OpenMode.ForWrite);
        line.EndPoint = new Point3d(3, 2, 0);
        transaction.Commit();
        document.Editor.WriteMessage("\nCDT N4 block definition mutated for fingerprint sensitivity probe");
    }

    private static ObjectId Add(
        BlockTableRecord owner,
        Entity entity,
        string key,
        Dictionary<string, object> evidence,
        Transaction transaction
    )
    {
        ObjectId id = owner.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
        string pid = PidStorage.GetOrCreateEntityPid(entity, transaction);
        evidence[key] = new { pid, handle = entity.Handle.ToString() };
        return id;
    }
}
