/* Public entry point of the TALYX design system, re-exported through
   `@talyx/sdk` so modules share one React instance with the host. */
export { TxIcon, txIconNames, type IconName } from "./icons";
export { installUiStyles, TX_ROOT_CLASS } from "./inject";
export { applyThemeVariables, isThemeVariable, tokenValue, type ThemeVariables } from "./theme";
export { createViewUnavailable } from "./unavailable";
export {
  TxAlert, TxButton, TxCheckbox, TxColorInput, TxDetails, TxDialog, TxDisclosure, TxEmpty, TxField, TxIconButton, TxInput,
  TxContextMenu, TxMenu, TxPanel, TxPopover, TxProgress, TxRoot, TxRow, TxSegmented, TxSelect, TxSheet, TxSkeleton,
  TxStatus, TxSwitch, TxTable, TxTabs, TxTd, TxTextarea, TxTh, TxToastProvider, TxTooltip, TxFieldLayout,
  TxTooltipProvider, useTxToast, TxSettingsGroup, TxSettingsNote, TxSettingsPage,
  type TxButtonProps, type TxCheckboxProps, type TxEmptyProps, type TxFieldProps, type TxIconButtonProps,
  type TxContextMenuProps, type TxInputProps, type TxColorInputProps, type TxMenuItem, type TxPopoverProps, type TxSearchProps, type TxSegmentedProps, type TxSwitchProps,
  type TxTabsProps, type TxToastOptions, type TxOverlaySize, type TxDensity,
} from "./components";
export { TxSearch } from "./components";

export { TxToolbar, TxToolbarDivider, TxSplit, TxInspector, TxSection } from "./workbench";
export {
  TxSpacer, TxObjectTitle, TxRunButton, TxRunProgress, TxLive, TxRunPicker, TxCompare, TxRunStrip, TxKeyNumbers, txDelta,
  TxChecks, TxParams, TxParamLayer, TxParamGroup, TxParamRow, TxParamValue, TxLinkedInputs, TxRunTable, TxTag, TxStateWord, TxPeriods,
  TxHeatmap, TxFailure, TxRegionHead,
  type TxRunKind, type TxRunStep, type TxKeyNumber, type TxCheck, type TxLinkedInput, type TxRunColumn, type TxRunRow, type TxRunGroup, type TxPeriodState, type TxHeatCell,
} from "./research";
