// NativeEntityExtractor — N4 supported entity families normalized into deterministic wire maps.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:02

using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace CDT.AutoCAD.Bridge;

internal static class NativeEntityExtractor
{
    internal static Dictionary<string, object?> Extract(
        Entity entity,
        string semanticPid,
        string ownerSpace,
        Transaction transaction
    )
    {
        Dictionary<string, object?> geometry;
        Dictionary<string, object?> metrics = [];
        string entityType;

        switch (entity)
        {
            case Line line:
                entityType = "LINE";
                double[] start = SemanticValue.Point(line.StartPoint);
                double[] end = SemanticValue.Point(line.EndPoint);
                if (string.CompareOrdinal(
                    SemanticFingerprint.CanonicalLinearSortKey(end),
                    SemanticFingerprint.CanonicalLinearSortKey(start)
                ) < 0)
                {
                    (start, end) = (end, start);
                }
                geometry = new Dictionary<string, object?>
                {
                    ["start"] = start,
                    ["end"] = end,
                };
                metrics["length"] = line.Length;
                break;

            case Circle circle:
                entityType = "CIRCLE";
                geometry = new Dictionary<string, object?>
                {
                    ["center"] = SemanticValue.Point(circle.Center),
                    ["normal"] = SemanticValue.Vector(circle.Normal),
                    ["radius"] = circle.Radius,
                };
                metrics["radius"] = circle.Radius;
                metrics["circumference"] = 2.0 * Math.PI * circle.Radius;
                metrics["area"] = Math.PI * circle.Radius * circle.Radius;
                break;

            case Arc arc:
                entityType = "ARC";
                geometry = new Dictionary<string, object?>
                {
                    ["center"] = SemanticValue.Point(arc.Center),
                    ["normal"] = SemanticValue.Vector(arc.Normal),
                    ["radius"] = arc.Radius,
                    ["start_angle"] = arc.StartAngle,
                    ["end_angle"] = arc.EndAngle,
                    ["sweep_angle"] = arc.TotalAngle,
                };
                metrics["radius"] = arc.Radius;
                metrics["length"] = arc.GetDistanceAtParameter(arc.EndParam)
                    - arc.GetDistanceAtParameter(arc.StartParam);
                break;

            case Polyline polyline:
                entityType = "LWPOLYLINE";
                List<Dictionary<string, object?>> vertices = [];
                for (int index = 0; index < polyline.NumberOfVertices; index++)
                {
                    Point2d point = polyline.GetPoint2dAt(index);
                    vertices.Add(new Dictionary<string, object?>
                    {
                        ["point"] = new[] { point.X, point.Y },
                        ["bulge"] = polyline.GetBulgeAt(index),
                        ["start_width"] = polyline.GetStartWidthAt(index),
                        ["end_width"] = polyline.GetEndWidthAt(index),
                    });
                }
                geometry = new Dictionary<string, object?>
                {
                    ["vertices"] = vertices,
                    ["closed"] = polyline.Closed,
                    ["elevation"] = polyline.Elevation,
                    ["normal"] = SemanticValue.Vector(polyline.Normal),
                };
                metrics["length"] = polyline.Length;
                if (polyline.Closed)
                {
                    try { metrics["area"] = polyline.Area; }
                    catch { metrics["area"] = null; }
                }
                break;

            case DBText text:
                entityType = "TEXT";
                geometry = new Dictionary<string, object?>
                {
                    ["text"] = text.TextString,
                    ["position"] = SemanticValue.Point(text.Position),
                    ["height"] = text.Height,
                    ["rotation"] = text.Rotation,
                    ["normal"] = SemanticValue.Vector(text.Normal),
                    ["text_style"] = SemanticValue.SymbolName(text.TextStyleId, transaction),
                };
                metrics["height"] = text.Height;
                break;

            case MText mtext:
                entityType = "MTEXT";
                geometry = new Dictionary<string, object?>
                {
                    ["text"] = mtext.Contents,
                    ["location"] = SemanticValue.Point(mtext.Location),
                    ["height"] = mtext.TextHeight,
                    ["rotation"] = mtext.Rotation,
                    ["normal"] = SemanticValue.Vector(mtext.Normal),
                    ["text_style"] = SemanticValue.SymbolName(mtext.TextStyleId, transaction),
                    ["attachment"] = mtext.Attachment.ToString(),
                };
                metrics["height"] = mtext.TextHeight;
                break;

            case BlockReference blockReference:
                entityType = "INSERT";
                geometry = new Dictionary<string, object?>
                {
                    ["definition"] = SemanticValue.SymbolName(blockReference.BlockTableRecord, transaction),
                    ["position"] = SemanticValue.Point(blockReference.Position),
                    ["rotation"] = blockReference.Rotation,
                    ["scale"] = new[]
                    {
                        blockReference.ScaleFactors.X,
                        blockReference.ScaleFactors.Y,
                        blockReference.ScaleFactors.Z,
                    },
                    ["normal"] = SemanticValue.Vector(blockReference.Normal),
                    ["transform"] = blockReference.BlockTransform.ToArray(),
                    ["attributes"] = ExtractAttributes(blockReference, transaction),
                };
                break;

            case Dimension dimension:
                entityType = "DIMENSION";
                geometry = new Dictionary<string, object?>
                {
                    ["dimension_type"] = dimension.GetType().Name,
                    ["measurement"] = dimension.Measurement,
                    ["text_override"] = dimension.DimensionText,
                    ["text_position"] = SemanticValue.Point(dimension.TextPosition),
                    ["normal"] = SemanticValue.Vector(dimension.Normal),
                    ["dimension_style"] = SemanticValue.SymbolName(dimension.DimensionStyle, transaction),
                };
                metrics["measurement"] = dimension.Measurement;
                break;

            case Hatch hatch:
                entityType = "HATCH";
                geometry = new Dictionary<string, object?>
                {
                    ["pattern_name"] = hatch.PatternName,
                    ["pattern_type"] = hatch.PatternType.ToString(),
                    ["associative"] = hatch.Associative,
                    ["loop_count"] = hatch.NumberOfLoops,
                    ["normal"] = SemanticValue.Vector(hatch.Normal),
                    ["elevation"] = hatch.Elevation,
                };
                try { metrics["area"] = hatch.Area; }
                catch { metrics["area"] = null; }
                break;

            default:
                throw new BridgeServiceException(
                    "UNSUPPORTED_ENTITY_TYPE",
                    $"native semantic snapshot does not support {entity.GetType().Name} in N4"
                );
        }

        return new Dictionary<string, object?>
        {
            ["semantic_pid"] = semanticPid,
            ["native_handle"] = entity.Handle.ToString(),
            ["entity_type"] = entityType,
            ["layer"] = entity.Layer,
            ["geometry"] = geometry,
            ["bbox"] = SemanticValue.Extents(entity),
            ["metrics"] = metrics,
            ["style"] = new Dictionary<string, object?>
            {
                ["linetype"] = entity.Linetype,
                ["lineweight"] = entity.LineWeight.ToString(),
                ["color_index"] = entity.Color.ColorIndex,
                ["visible"] = entity.Visible,
            },
            ["hierarchy"] = new Dictionary<string, object?>
            {
                ["owner_space"] = ownerSpace,
            },
        };
    }

    private static List<Dictionary<string, object?>> ExtractAttributes(
        BlockReference blockReference,
        Transaction transaction
    )
    {
        List<Dictionary<string, object?>> attributes = [];
        foreach (ObjectId attributeId in blockReference.AttributeCollection)
        {
            if (transaction.GetObject(attributeId, OpenMode.ForRead, false) is AttributeReference attribute)
            {
                attributes.Add(new Dictionary<string, object?>
                {
                    ["tag"] = attribute.Tag,
                    ["text"] = attribute.TextString,
                    ["position"] = SemanticValue.Point(attribute.Position),
                });
            }
        }
        attributes.Sort((left, right) => string.CompareOrdinal(
            Convert.ToString(left["tag"]),
            Convert.ToString(right["tag"])
        ));
        return attributes;
    }
}
