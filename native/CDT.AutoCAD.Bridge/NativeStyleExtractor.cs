// NativeStyleExtractor — N4 deterministic layer/linetype/text/dimension style extraction.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:05

using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.Bridge;

internal static class NativeStyleExtractor
{
    internal static List<Dictionary<string, object?>> Extract(
        Database database,
        Transaction transaction
    )
    {
        List<Dictionary<string, object?>> styles = [];

        LayerTable layers = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        foreach (ObjectId id in layers)
        {
            LayerTableRecord layer = (LayerTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            styles.Add(new Dictionary<string, object?>
            {
                ["kind"] = "layer",
                ["name"] = layer.Name,
                ["is_off"] = layer.IsOff,
                ["is_frozen"] = layer.IsFrozen,
                ["is_locked"] = layer.IsLocked,
                ["is_plottable"] = layer.IsPlottable,
                ["color_index"] = layer.Color.ColorIndex,
                ["linetype"] = SemanticValue.SymbolName(layer.LinetypeObjectId, transaction),
                ["lineweight"] = layer.LineWeight.ToString(),
            });
        }

        LinetypeTable linetypes = (LinetypeTable)transaction.GetObject(
            database.LinetypeTableId,
            OpenMode.ForRead
        );
        foreach (ObjectId id in linetypes)
        {
            LinetypeTableRecord linetype = (LinetypeTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            styles.Add(new Dictionary<string, object?>
            {
                ["kind"] = "linetype",
                ["name"] = linetype.Name,
                ["description"] = linetype.AsciiDescription,
                ["pattern_length"] = linetype.PatternLength,
                ["dash_count"] = linetype.NumDashes,
            });
        }

        TextStyleTable textStyles = (TextStyleTable)transaction.GetObject(
            database.TextStyleTableId,
            OpenMode.ForRead
        );
        foreach (ObjectId id in textStyles)
        {
            TextStyleTableRecord style = (TextStyleTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            styles.Add(new Dictionary<string, object?>
            {
                ["kind"] = "text_style",
                ["name"] = style.Name,
                ["file_name"] = style.FileName,
                ["big_font_file_name"] = style.BigFontFileName,
                ["text_size"] = style.TextSize,
                ["x_scale"] = style.XScale,
                ["obliquing_angle"] = style.ObliquingAngle,
            });
        }

        DimStyleTable dimStyles = (DimStyleTable)transaction.GetObject(
            database.DimStyleTableId,
            OpenMode.ForRead
        );
        foreach (ObjectId id in dimStyles)
        {
            DimStyleTableRecord style = (DimStyleTableRecord)transaction.GetObject(id, OpenMode.ForRead);
            styles.Add(new Dictionary<string, object?>
            {
                ["kind"] = "dimension_style",
                ["name"] = style.Name,
                ["dimscale"] = style.Dimscale,
                ["text_height"] = style.Dimtxt,
            });
        }

        styles.Sort((left, right) => string.CompareOrdinal(
            SemanticFingerprint.CanonicalScalarSortKey(left),
            SemanticFingerprint.CanonicalScalarSortKey(right)
        ));
        return styles;
    }
}
