"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { RegionPicker } from "@/components/region-picker";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usersApi } from "@/lib/api";
import type { Role, User } from "@/lib/types";

type BaseProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
};

function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="text-destructive text-sm" role="alert">
      {message}
    </p>
  );
}

function Spinner() {
  return <LoaderCircle className="size-4 animate-spin" />;
}

/** 新建用户。 */
export function CreateUserDialog({ open, onOpenChange, onDone }: BaseProps) {
  const [account, setAccount] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setAccount("");
      setDisplayName("");
      setPassword("");
      setError(null);
    }
  }, [open]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await usersApi.create({
        account: account.trim(),
        display_name: displayName.trim(),
        password,
      });
      onOpenChange(false);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建用户</DialogTitle>
          <DialogDescription>
            账号只能是字母/数字/下划线/连字符；中文请填在用户名里。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="new-account">账号</Label>
            <Input
              id="new-account"
              required
              minLength={3}
              maxLength={32}
              pattern="[a-zA-Z0-9_\-]+"
              placeholder="例如 zhangsan"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-name">用户名</Label>
            <Input
              id="new-name"
              required
              maxLength={64}
              placeholder="例如 张三（支持中文）"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-password">初始密码</Label>
            <Input
              id="new-password"
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <ErrorText message={error} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Spinner />}
              创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type EditProps = BaseProps & {
  user: User | null;
  roles: Role[];
  canAssignRole: boolean;
  canManageCities: boolean;
};

/** 编辑用户：基本信息 + 角色 + 关注城市，按权限逐段显示。 */
export function EditUserDialog({
  user,
  roles,
  canAssignRole,
  canManageCities,
  open,
  onOpenChange,
  onDone,
}: EditProps) {
  const [displayName, setDisplayName] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [roleCodes, setRoleCodes] = useState<string[]>([]);
  const [regionCodes, setRegionCodes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open && user) {
      setDisplayName(user.display_name);
      setIsActive(user.is_active);
      setRoleCodes(user.roles.map((r) => r.code));
      setRegionCodes(user.followed_region_codes);
      setError(null);
    }
  }, [open, user]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      if (displayName !== user.display_name || isActive !== user.is_active) {
        await usersApi.update(user.id, { display_name: displayName.trim(), is_active: isActive });
      }
      if (canAssignRole) {
        const before = user.roles.map((r) => r.code).sort().join(",");
        if ([...roleCodes].sort().join(",") !== before) {
          await usersApi.setRoles(user.id, roleCodes);
        }
      }
      if (canManageCities) {
        if ([...regionCodes].sort().join(",") !== [...user.followed_region_codes].sort().join(",")) {
          await usersApi.setFollowedRegions(user.id, regionCodes);
        }
      }
      onOpenChange(false);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>编辑用户</DialogTitle>
          <DialogDescription>
            账号 <span className="font-mono">{user?.account}</span> 不可修改。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-1.5">
            <Label htmlFor="edit-name">用户名</Label>
            <Input
              id="edit-name"
              required
              maxLength={64}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>

          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
            启用该账号
          </label>

          <div className="space-y-2 border-t pt-4">
            <div className="text-sm font-medium">角色</div>
            {!canAssignRole && (
              <p className="text-muted-foreground text-xs">你没有分配角色的权限，仅可查看。</p>
            )}
            <div className="grid gap-1.5 sm:grid-cols-2">
              {roles.map((role) => (
                <label key={role.id} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    disabled={!canAssignRole}
                    checked={roleCodes.includes(role.code)}
                    onChange={(e) =>
                      setRoleCodes((prev) =>
                        e.target.checked
                          ? [...prev, role.code]
                          : prev.filter((code) => code !== role.code)
                      )
                    }
                  />
                  <span>
                    {role.name}
                    <span className="text-muted-foreground ml-1 text-xs">{role.code}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2 border-t pt-4">
            <div className="text-sm font-medium">关注城市</div>
            {!canManageCities && (
              <p className="text-muted-foreground text-xs">你没有维护关注城市的权限，仅可查看。</p>
            )}
            <RegionPicker
              value={regionCodes}
              onChange={setRegionCodes}
              disabled={!canManageCities}
            />
          </div>

          <ErrorText message={error} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Spinner />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 重置密码。 */
export function ResetPasswordDialog({
  user,
  open,
  onOpenChange,
  onDone,
}: BaseProps & { user: User | null }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setPassword("");
      setError(null);
    }
  }, [open]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      await usersApi.resetPassword(user.id, password);
      onOpenChange(false);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重置失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重置密码</DialogTitle>
          <DialogDescription>
            为 <span className="font-mono">{user?.account}</span> 设置新密码，旧密码立即失效。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="reset-password">新密码</Label>
            <Input
              id="reset-password"
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <ErrorText message={error} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Spinner />}
              确认重置
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 删除确认。 */
export function DeleteUserDialog({
  user,
  open,
  onOpenChange,
  onDone,
}: BaseProps & { user: User | null }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  async function confirm() {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      await usersApi.remove(user.id);
      onOpenChange(false);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>删除用户</DialogTitle>
          <DialogDescription>
            将删除 <span className="font-mono">{user?.account}</span>（{user?.display_name}
            ）及其关注城市。他历史上的操作仍会保留在审计日志中。
          </DialogDescription>
        </DialogHeader>
        <ErrorText message={error} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button variant="destructive" onClick={confirm} disabled={busy}>
            {busy && <Spinner />}
            确认删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
