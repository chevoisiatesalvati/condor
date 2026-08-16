import type { RoutineFieldInfo } from "@/lib/api";
import { GroupedRoutineConfigForm } from "@/components/routines/GroupedRoutineConfigForm";
import { RoutineConfigForm } from "@/components/routines/RoutineConfigForm";

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  groups?: string[];
  expandedGroups?: string[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

export function RoutineConfigFormShell({
  fields,
  groups,
  expandedGroups,
  values,
  onChange,
}: Props) {
  const useGrouped =
    !!groups?.length &&
    Object.values(fields).some(
      (field) => !!field.group && groups.includes(field.group),
    );

  if (useGrouped) {
    return (
      <GroupedRoutineConfigForm
        fields={fields}
        groups={groups!}
        expandedGroups={expandedGroups}
        values={values}
        onChange={onChange}
      />
    );
  }
  return <RoutineConfigForm fields={fields} values={values} onChange={onChange} />;
}
