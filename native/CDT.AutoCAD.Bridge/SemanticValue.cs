// SemanticValue — small native-to-wire normalization helpers for N4 semantic extraction.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:00

using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace CDT.AutoCAD.Bridge;

internal static class SemanticValue
{
    internal static double[] Point(Point3d point) => [point.X, point.Y, point.Z];

    internal static double[] Vector(Vector3d vector) => [vector.X, vector.Y, vector.Z];

    internal static string SymbolName(ObjectId id, Transaction transaction)
    {
        if (id.IsNull)
        {
            return string.Empty;
        }
        DBObject value = transaction.GetObject(id, OpenMode.ForRead, false);
        return value is SymbolTableRecord symbol ? symbol.Name : value.Handle.ToString();
    }

    internal static Dictionary<string, object?>? Extents(Entity entity)
    {
        try
        {
            Extents3d extents = entity.GeometricExtents;
            return new Dictionary<string, object?>
            {
                ["min"] = Point(extents.MinPoint),
                ["max"] = Point(extents.MaxPoint),
            };
        }
        catch
        {
            return null;
        }
    }

    internal static Dictionary<string, object?>? CombineExtents(
        IReadOnlyList<Dictionary<string, object?>> entities
    )
    {
        double[]? minimum = null;
        double[]? maximum = null;
        foreach (Dictionary<string, object?> entity in entities)
        {
            if (entity["bbox"] is not Dictionary<string, object?> bbox
                || bbox["min"] is not double[] itemMin
                || bbox["max"] is not double[] itemMax)
            {
                continue;
            }
            if (minimum is null)
            {
                minimum = (double[])itemMin.Clone();
                maximum = (double[])itemMax.Clone();
                continue;
            }
            for (int axis = 0; axis < 3; axis++)
            {
                minimum[axis] = Math.Min(minimum[axis], itemMin[axis]);
                maximum![axis] = Math.Max(maximum[axis], itemMax[axis]);
            }
        }
        return minimum is null || maximum is null
            ? null
            : new Dictionary<string, object?> { ["min"] = minimum, ["max"] = maximum };
    }

    internal static string UnitName(UnitsValue value) => value.ToString().ToLowerInvariant();
}
