"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export type Column<T> = {
  key: string;
  label: string;
  render?: (row: T) => React.ReactNode;
  className?: string;
};

type Props<T> = {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty?: string;
  loading?: boolean;
};

/** 共享数据表：有界分页由页面控制，表格自身不撑破窄屏。 */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  empty = "当前窗口没有数据",
  loading,
}: Props<T>) {
  return (
    <div className="rounded-lg border" aria-busy={loading}>
      <Table style={{ minWidth: `${Math.max(640, columns.length * 110)}px` }}>
        <caption className="text-muted-foreground px-3 pb-2 text-left text-xs">
          {caption}
        </caption>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead key={column.key} className={column.className}>
                {column.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={columns.length}
                className="text-muted-foreground py-8 text-center text-sm"
              >
                {loading ? "正在加载…" : empty}
              </TableCell>
            </TableRow>
          ) : (
            rows.map((row) => (
              <TableRow key={rowKey(row)}>
                {columns.map((column) => (
                  <TableCell key={column.key} className={column.className}>
                    {column.render
                      ? column.render(row)
                      : String((row as Record<string, unknown>)[column.key] ?? "—")}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
