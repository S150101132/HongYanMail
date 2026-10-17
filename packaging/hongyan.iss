; 鸿雁 per-user installer (Inno Setup 6). No administrator rights needed.
;
; Built by packaging/build.ps1 after all checks pass on dist\Hongyan:
;   ISCC.exe /DAppVersion=0.1.0a1 packaging\hongyan.iss   ->  dist\Hongyan-Setup-<version>.exe
;
; User data is never removed silently: the uninstaller deletes it only when the
; user ticks the box. Data lives in %LOCALAPPDATA%\CoremailDesktopPreview, or in
; HongyanData beside the program on machines where the profile is not writable
; (see _data_root in coremail_desktop/bootstrap.py).

#ifndef AppVersion
  #define AppVersion "0.1.0a1"
#endif
#define AppExe "Hongyan.exe"

[Setup]
AppId={{DDD65C07-9CFC-44D6-A0C9-36CDD453628C}
AppName=鸿雁 · 工作邮箱
AppVersion={#AppVersion}
AppVerName=鸿雁 {#AppVersion}
AppPublisher=鸿雁
DefaultDirName={autopf}\Hongyan
DefaultGroupName=鸿雁
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Hongyan-Setup-{#AppVersion}
SetupIconFile=hongyan.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName=鸿雁 · 工作邮箱
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The running client holds this mutex (coremail_desktop/app/single_instance.py).
AppMutex=Hongyan.SingleInstance
CloseApplications=yes
; Managed machines may refuse registry writes, and a failed uninstall key aborts the
; whole install. Uninstall instead via {app}\Uninstall-Hongyan.cmd (or the Start-menu
; entry when shortcuts could be created).
CreateUninstallRegKey=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
DirNote=如果本机安全策略禁止程序写入用户目录（AppData），请选择其他磁盘上的文件夹，例如 D:\Programs\Hongyan。
StartMenuIcon=创建开始菜单快捷方式（本机若禁止写入用户目录会跳过）
DesktopIcon=创建桌面快捷方式
LaunchNow=立即启动鸿雁
RemoveDataTitle=卸载鸿雁
RemoveDataText=鸿雁已从本机移除。本机保存的邮件缓存、草稿和登录信息（均已加密）默认保留，重新安装后可继续使用。
RemoveDataBox=同时删除本机的鸿雁数据（不可恢复）
RemoveDataOk=确定

[Tasks]
Name: "startmenuicon"; Description: "{cm:StartMenuIcon}"
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[Files]
Source: "..\dist\Hongyan\*"; DestDir: "{app}"; Excludes: "HongyanData\*,debug.log"; Flags: ignoreversion recursesubdirs createallsubdirs
; The uninstaller copies itself to %TEMP% first; this wrapper redirects TEMP for locked-down machines.
Source: "Uninstall-Hongyan.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\鸿雁"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: startmenuicon
Name: "{autodesktop}\鸿雁"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autoprograms}\卸载鸿雁"; Filename: "{app}\Uninstall-Hongyan.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Tasks: startmenuicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
var
  Note: TNewStaticText;
begin
  Note := TNewStaticText.Create(WizardForm);
  Note.Parent := WizardForm.SelectDirPage;
  Note.Left := WizardForm.DirEdit.Left;
  Note.Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + ScaleY(16);
  Note.Width := WizardForm.SelectDirPage.ClientWidth - Note.Left;
  Note.AutoSize := False;
  Note.WordWrap := True;
  Note.Height := ScaleY(48);
  Note.Caption := CustomMessage('DirNote');
end;

function AskRemoveData: Boolean;
var
  Form: TSetupForm;
  Text: TNewStaticText;
  Box: TNewCheckBox;
  Ok: TNewButton;
begin
  Result := False;
  Form := CreateCustomForm(ScaleX(420), ScaleY(150), False, True);
  try
    Form.Caption := CustomMessage('RemoveDataTitle');
    Text := TNewStaticText.Create(Form);
    Text.Parent := Form;
    Text.Left := ScaleX(16); Text.Top := ScaleY(16);
    Text.Width := Form.ClientWidth - ScaleX(32); Text.Height := ScaleY(48);
    Text.AutoSize := False; Text.WordWrap := True;
    Text.Caption := CustomMessage('RemoveDataText');
    Box := TNewCheckBox.Create(Form);
    Box.Parent := Form;
    Box.Left := ScaleX(16); Box.Top := ScaleY(72);
    Box.Width := Form.ClientWidth - ScaleX(32); Box.Height := ScaleY(20);
    Box.Caption := CustomMessage('RemoveDataBox');
    Box.Checked := False;
    Ok := TNewButton.Create(Form);
    Ok.Parent := Form;
    Ok.Caption := CustomMessage('RemoveDataOk');
    Ok.Width := ScaleX(88); Ok.Height := ScaleY(26);
    Ok.Left := Form.ClientWidth - Ok.Width - ScaleX(16);
    Ok.Top := Form.ClientHeight - Ok.Height - ScaleY(12);
    Ok.ModalResult := mrOk; Ok.Default := True;
    Form.ActiveControl := Ok;
    Form.ShowModal;
    Result := Box.Checked;
  finally
    Form.Free;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    { 开机自动启动 points at this copy; remove it with the program. }
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Hongyan');
  if CurUninstallStep = usPostUninstall then
  begin
    if (not UninstallSilent) and AskRemoveData then
    begin
      DelTree(ExpandConstant('{localappdata}\CoremailDesktopPreview'), True, True, True);
      DelTree(ExpandConstant('{app}\HongyanData'), True, True, True);
    end;
    RemoveDir(ExpandConstant('{app}'));  { Only if empty: kept user data stays. }
  end;
end;
