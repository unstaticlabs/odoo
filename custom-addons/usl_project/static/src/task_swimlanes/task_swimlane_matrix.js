/** Stable native grouping identities; labels and ephemeral datapoint IDs are not keys. */
export function groupValue(group) {
    const value = group._rawValue;
    return Array.isArray(value) ? value[0] : value;
}
export function groupKey(spec, group) {
    const value = groupValue(group);
    return JSON.stringify([spec, typeof value, value, group.range?.from?.toISO(), group.range?.to?.toISO()]);
}
export function buildMatrix(list) {
    const rows = new Map();
    for (const column of list.groups) {
        for (const group of column.list.groups) {
            const key = groupKey(list.groupBy[1], group);
            if (!rows.has(key)) rows.set(key, { key, group });
        }
    }
    return [...rows.values()].map((row) => ({ ...row, cells: list.groups.map((column) => {
        const leaf = column.list.groups.find((group) => groupKey(list.groupBy[1], group) === row.key);
        return { id: JSON.stringify([column.id, row.key]), column, row: row.group, leaf,
            status: leaf ? (leaf.isFolded ? 'closed' : 'loaded') :
                (!column.isFolded && column.list.groups.length >= column.list.count ? 'empty' : 'unloaded') };
    }) }));
}
