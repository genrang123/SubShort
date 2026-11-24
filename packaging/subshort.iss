[Setup]
AppId={{38C866C4-1D14-4EF4-AE7B-2B26CFDA013E}
AppName=SubShort
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\SubShort
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
UninstallDisplayIcon={app}\SubShort.exe
OutputDir=..\dist\installer
OutputBaseFilename=SubShort-{#AppVersion}-Windows-x64-Setup
Compression=lzma2/fast
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=yes
LanguageDetectionMethod=none
UsePreviousLanguage=no

[Languages]
Name: "vi"; MessagesFile: "languages\Vietnamese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
vi.LaunchSubShort=Mở SubShort
vi.DataTitle=Dữ liệu SubShort
vi.DataDescription=Đã tìm thấy dữ liệu của SubShort trên máy này.
vi.DataInstructions=Chọn cách xử lý dữ liệu cũ. Cài như mới sẽ chuyển dữ liệu cũ sang thư mục sao lưu, không xóa tệp.
vi.KeepData=Ghi đè chương trình, giữ dữ liệu cũ (khuyến nghị)
vi.FreshData=Cài như mới, sao lưu dữ liệu cũ
vi.InstallOutsideData=Hãy chọn thư mục cài đặt nằm ngoài thư mục dữ liệu SubShort.
vi.CloseSubShort=Hãy đóng SubShort trước khi cài như mới.
vi.BackupFailed=Không thể sao lưu dữ liệu SubShort. Dữ liệu cũ vẫn được giữ nguyên. Hãy đóng SubShort và cài lại.
vi.BackupDone=Dữ liệu cũ đã được sao lưu tại:
en.LaunchSubShort=Launch SubShort
en.DataTitle=SubShort data
en.DataDescription=Existing SubShort data was found on this computer.
en.DataInstructions=Choose what to do with your existing data. A fresh install moves it to a backup folder without deleting files.
en.KeepData=Replace the program and keep existing data (recommended)
en.FreshData=Start fresh and back up existing data
en.InstallOutsideData=Choose an installation folder outside the SubShort data folder.
en.CloseSubShort=Close SubShort before starting a fresh install.
en.BackupFailed=Could not back up SubShort data. Existing data is unchanged. Close SubShort and run Setup again.
en.BackupDone=Existing data was backed up to:

[Files]
Source: "..\build\installer\payload\SubShort.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\installer\payload\media\*"; DestDir: "{app}\media"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\installer\payload\ModelASR\faster-whisper-small\*"; DestDir: "{app}\ModelASR\faster-whisper-small"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{userprograms}\SubShort"; Filename: "{app}\SubShort.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\SubShort.exe"; WorkingDir: "{app}"; Description: "{cm:LaunchSubShort}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
var
  DataPage: TInputOptionWizardPage;
  DataDir: String;

procedure InitializeWizard;
begin
  DataDir := ExpandConstant('{localappdata}\SubShort');
  DataPage := CreateInputOptionPage(wpSelectDir,
    CustomMessage('DataTitle'), CustomMessage('DataDescription'),
    CustomMessage('DataInstructions'), True, False);
  DataPage.Add(CustomMessage('KeepData'));
  DataPage.Add(CustomMessage('FreshData'));
  DataPage.SelectedValueIndex := 0;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = DataPage.ID) and (WizardSilent or not DirExists(DataDir));
end;

function FreshDataSelected: Boolean;
begin
  Result := not WizardSilent and (DataPage.SelectedValueIndex = 1) and DirExists(DataDir);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  InstallDir: String;
begin
  Result := '';
  if not FreshDataSelected then
    Exit;
  InstallDir := ExpandConstant('{app}');
  if PathSame(InstallDir, DataDir) or PathStartsWith(AddBackslash(InstallDir), AddBackslash(DataDir), True) then
    Result := CustomMessage('InstallOutsideData')
  else if FindWindowByWindowName('SubShort · Video Studio') <> 0 then
    Result := CustomMessage('CloseSubShort');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  BackupBase, BackupDir: String;
  Suffix: Integer;
begin
  // Leave data in place until setup has installed the application files.
  if (CurStep <> ssPostInstall) or not FreshDataSelected then
    Exit;
  BackupBase := DataDir + '.backup-' + GetDateTimeString('yyyymmdd-hhnnss', '-', ':');
  BackupDir := BackupBase;
  Suffix := 1;
  while FileOrDirExists(BackupDir) do
  begin
    BackupDir := BackupBase + '-' + IntToStr(Suffix);
    Suffix := Suffix + 1;
  end;
  if (FindWindowByWindowName('SubShort · Video Studio') <> 0) or not RenameFile(DataDir, BackupDir) then
    RaiseException(CustomMessage('BackupFailed'));
  MsgBox(CustomMessage('BackupDone') + #13#10 + BackupDir, mbInformation, MB_OK);
end;
