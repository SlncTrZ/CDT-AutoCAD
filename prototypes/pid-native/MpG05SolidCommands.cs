// MpG05SolidCommands — disposable native 3DSOLID semantic fixture for MP-G05.
// Wing: ops | Topic: native-solid-semantic | Updated: 2026-09-12

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: CommandClass(typeof(CDT.AutoCAD.PidProbe.MpG05SolidCommands))]

namespace CDT.AutoCAD.PidProbe;

public static class MpG05SolidCommands
{
    [CommandMethod("CDT_MPG05_CREATE_SOLID_FIXTURE", CommandFlags.Modal)]
    public static void CreateSolidFixture()
    {
        Document document = AcApplication.DocumentManager.MdiActiveDocument
            ?? throw new InvalidOperationException("No active AutoCAD document");
        Database database = document.Database;
        string documentPid;
        string solidPid;
        string solidHandle;

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

            Solid3d solid = new();
            solid.CreateBox(10.0, 8.0, 6.0);
            solid.TransformBy(Matrix3d.Displacement(new Vector3d(5.0, 7.0, 3.0)));
            model.AppendEntity(solid);
            transaction.AddNewlyCreatedDBObject(solid, true);
            solidPid = PidStorage.GetOrCreateEntityPid(solid, transaction);
            solidHandle = solid.Handle.ToString();
            transaction.Commit();
        }

        string report = ProbeReport.Write(
            "mp-g05-solid-fixture",
            new
            {
                status = "PASS",
                document_pid = documentPid,
                solid = new
                {
                    pid = solidPid,
                    handle = solidHandle,
                    expected_volume = 480.0,
                },
            }
        );
        document.Editor.WriteMessage($"\nCDT MP-G05 solid fixture: {report}");
    }
}
