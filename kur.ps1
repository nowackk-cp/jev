<#
.SYNOPSIS
    Jev'i bu klasörden kurar. İnternetten hiçbir şey indirmez.

.DESCRIPTION
    1. Python 3.11 ya da üstünü bulur (önce py -3, sonra python ve python3).
    2. Jev'i düzenlenebilir kipte kurar: python -m pip install --no-index -e <bu klasör>.
       Depodaki kod doğrudan kullanılır; kodu güncelleyince yeniden kurmak gerekmez.
       Kaldırmak için: py -m pip uninstall jev
    3. jev.exe'yi bulur ve klasörünün PATH'te olup olmadığını denetler.
       PATH'te değilse ne yapılacağını yazar; -YolaEkle verilirse klasörü kullanıcı PATH'ine kendisi ekler.
    4. jev --surum ile dener ve sonraki adımları yazar.

.PARAMETER YolaEkle
    jev.exe'nin klasörü PATH'te değilse kullanıcı PATH'ine ekler (yönetici izni gerekmez).

.PARAMETER Python
    Kullanılacak python.exe'nin yolu. Verilmezse aranır.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\kur.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\kur.ps1 -YolaEkle
#>
[CmdletBinding()]
param(
    [switch]$YolaEkle,
    [string]$Python
)

$ErrorActionPreference = 'Continue'
$Kok = $PSScriptRoot
$EnAz = [version]'3.11'

# Python'a verilen kodlar tek satır ve çift tırnaksız (PowerShell 5.1 çift tırnağı yerli programlara bozuk geçirir).
# Çıktı JSON: Türkçe harfli yollar ASCII kaçışlarıyla gelir, konsol kod sayfası karıştırmaz.
$KodSurum = "import json,sys; print(json.dumps({'exe': sys.executable, 'surum': '%d.%d.%d' % sys.version_info[:3]}))"
$KodJevBul = "import json,os,sysconfig,importlib.metadata as m; d=m.distribution('jev'); " +
             "r=[str(d.locate_file(f).resolve().parent) for f in (d.files or []) if f.name.lower()=='jev.exe']; " +
             "s=[sysconfig.get_path('scripts'), sysconfig.get_path('scripts', sysconfig.get_preferred_scheme('user'))]; " +
             "print(json.dumps({'klasorler': [x for x in dict.fromkeys(r+s) if os.path.isfile(os.path.join(x, 'jev.exe'))]}))"

function Baslik([string]$metin) { Write-Host ''; Write-Host $metin -ForegroundColor Cyan }
function Tamam([string]$metin) { Write-Host "  + $metin" -ForegroundColor Green }
function Uyari([string]$metin) { Write-Host "  ! $metin" -ForegroundColor Yellow }
function Bilgi([string]$metin) { Write-Host "    $metin" }
function Dur([string]$metin) {
    Write-Host ''
    Write-Host "Kurulum durdu: $metin" -ForegroundColor Red
    exit 1
}

function Python-Yokla([string]$komut, [string[]]$onEk) {
    # Adayın gerçek yolunu ve sürümünü döndürür; çalışmazsa $null (ör. Microsoft Store'a yönlendiren python.exe).
    try {
        $cikti = & $komut @onEk -c $KodSurum 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or -not $cikti) { return $null }
    try {
        $bilgi = ($cikti | Select-Object -Last 1) | ConvertFrom-Json
        return [pscustomobject]@{ Exe = [string]$bilgi.exe; Surum = [version][string]$bilgi.surum }
    } catch {
        return $null
    }
}

function Yol-Normal([string]$yol) {
    $acik = [Environment]::ExpandEnvironmentVariables($yol.Trim().Trim('"'))
    return [IO.Path]::GetFullPath($acik).TrimEnd('\')
}

function Yolda-Mi([string]$klasor, [string]$yolDegeri) {
    # Klasör, ;'le ayrılmış PATH değerinde var mı? (büyük/küçük harf ve sondaki \ fark etmez)
    if (-not $yolDegeri) { return $false }
    $hedef = Yol-Normal $klasor
    foreach ($parca in ($yolDegeri -split ';')) {
        if (-not $parca.Trim()) { continue }
        try {
            if ((Yol-Normal $parca) -ieq $hedef) { return $true }
        } catch { }
    }
    return $false
}

function Kullanici-Yolu {
    # Kayıt defterindeki ham değer: %USERPROFILE% gibi değişkenler açılmadan, değer türü korunarak okunur.
    $anahtar = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
    if (-not $anahtar) { return '' }
    try {
        return [string]$anahtar.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    } finally {
        $anahtar.Close()
    }
}

function Kullanici-Yoluna-Ekle([string]$klasor) {
    $anahtar = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    try {
        $eski = [string]$anahtar.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $tur = [Microsoft.Win32.RegistryValueKind]::ExpandString
        if ($anahtar.GetValueNames() -contains 'Path') { $tur = $anahtar.GetValueKind('Path') }
        if ($eski.Trim()) { $yeni = $eski.TrimEnd(';') + ';' + $klasor } else { $yeni = $klasor }
        $anahtar.SetValue('Path', $yeni, $tur)
    } finally {
        $anahtar.Close()
    }
    # Açık programlara ortamın değiştiğini duyur (yeni açılan terminaller yeni PATH'i alsın).
    try {
        if (-not ('JevKur.Ortam' -as [type])) {
            Add-Type -Namespace JevKur -Name Ortam -MemberDefinition @'
[DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
                                               uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
        }
        $sonuc = [UIntPtr]::Zero
        [void][JevKur.Ortam]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$sonuc)
    } catch { }
}

Write-Host 'Jev kurulumu' -ForegroundColor Cyan
Bilgi "Klasör: $Kok"
if (-not (Test-Path -LiteralPath (Join-Path $Kok 'pyproject.toml'))) {
    Dur "pyproject.toml bulunamadı. kur.ps1'i Jev deposunun içinden çalıştırın."
}

# --- 1. Python ----------------------------------------------------------------------------------
Baslik '1/4  Python aranıyor'
if ($Python) {
    $adaylar = @(@{ Komut = $Python; OnEk = @() })
} else {
    $adaylar = @(
        @{ Komut = 'py'; OnEk = @('-3') },
        @{ Komut = 'python'; OnEk = @() },
        @{ Komut = 'python3'; OnEk = @() }
    )
}
$py = $null
$eskiler = @()
foreach ($aday in $adaylar) {
    if (-not (Get-Command $aday.Komut -ErrorAction SilentlyContinue)) { continue }
    $bilgi = Python-Yokla $aday.Komut $aday.OnEk
    if (-not $bilgi) { continue }
    if ($bilgi.Surum -ge $EnAz) { $py = $bilgi; break }
    $eskiler += "$($bilgi.Surum) ($($bilgi.Exe))"
}
if (-not $py) {
    if ($eskiler) { Uyari "Bulunan Python sürümleri çok eski: $($eskiler -join ', ')" }
    if ($Python) { Dur "Verilen Python çalışmadı ya da 3.11'den eski: $Python" }
    Dur ("Python 3.11 ya da üstü bulunamadı. https://www.python.org adresinden kurun (kurulumda 'py launcher' " +
         "seçili olsun), sonra bu betiği yeniden çalıştırın.")
}
Tamam "Python $($py.Surum): $($py.Exe)"

& $py.Exe -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Dur "Bu Python'da pip yok. Şunu çalıştırıp yeniden deneyin:  & '$($py.Exe)' -m ensurepip --upgrade"
}
if (Get-Command git -ErrorAction SilentlyContinue) {
    Tamam 'Git bulundu'
} else {
    Uyari "Git bulunamadı. Jev projeleri git ile yönetir; koşudan önce https://git-scm.com adresinden kurun."
}

# --- 2. Kurulum ---------------------------------------------------------------------------------
Baslik '2/4  Jev kuruluyor (düzenlenebilir kip, hiçbir şey indirilmez)'
& $py.Exe -m pip install --no-index --disable-pip-version-check -e $Kok
if ($LASTEXITCODE -ne 0) {
    Dur ("pip kurulumu başarısız oldu (çıkış kodu $LASTEXITCODE); ayrıntı yukarıdaki pip çıktısında. " +
         "Açık bir jev koşusu varsa kapatıp yeniden deneyin.")
}
Tamam 'Jev kuruldu'

# --- 3. jev komutu ------------------------------------------------------------------------------
Baslik '3/4  jev komutu denetleniyor'
$klasor = $null
try {
    $bulunan = (& $py.Exe -c $KodJevBul 2>$null | Select-Object -Last 1) | ConvertFrom-Json
    foreach ($k in $bulunan.klasorler) { $klasor = [string]$k; break }
} catch { }
if (-not $klasor) {
    Dur "Kurulum bitti ama jev.exe bulunamadı. Bunun yerine şu çalışır:  & '$($py.Exe)' -m jev --yardim"
}
$jevExe = Join-Path $klasor 'jev.exe'
Tamam "jev.exe: $jevExe"

$yoldaki = Get-Command jev -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$jev = 'jev'  # sonraki adımlarda gösterilecek komut
if ($yoldaki -and ((Yol-Normal $yoldaki.Source) -ieq (Yol-Normal $jevExe))) {
    Tamam 'jev komutu PATH üzerinden çalışıyor'
} elseif ($yoldaki) {
    Uyari "PATH'te önce başka bir jev geliyor: $($yoldaki.Source)"
    Bilgi "Bu kurulumu kullanmak için o kopyayı kaldırın ya da PATH'te '$klasor' klasörünü öne alın."
    $jev = "& '$jevExe'"
} elseif (Yolda-Mi $klasor (Kullanici-Yolu)) {
    Uyari "'$klasor' kullanıcı PATH'inde var ama bu terminal eski PATH'i kullanıyor."
    Bilgi 'Yeni bir terminal açınca jev komutu çalışır.'
} elseif ($YolaEkle) {
    Kullanici-Yoluna-Ekle $klasor
    $env:Path = $env:Path.TrimEnd(';') + ';' + $klasor
    Tamam "'$klasor' kullanıcı PATH'ine eklendi"
    Bilgi 'Yeni açacağınız terminallerde jev komutu çalışır (bu pencereyi kapatıp yeniden açın).'
} else {
    Uyari "jev.exe'nin klasörü PATH'te değil: $klasor"
    Bilgi 'Seçenekler:'
    Bilgi "  - Betiği -YolaEkle ile yeniden çalıştırın (klasörü kullanıcı PATH'ine ekler):"
    Bilgi '      powershell -ExecutionPolicy Bypass -File .\kur.ps1 -YolaEkle'
    Bilgi "  - Tam yolla çalıştırın:  & '$jevExe' --yardim"
    Bilgi "  - Python modülü olarak:  & '$($py.Exe)' -m jev --yardim"
    $jev = "& '$jevExe'"
}

# --- 4. Deneme ----------------------------------------------------------------------------------
Baslik '4/4  Deneme: jev --surum'
& $jevExe --surum
if ($LASTEXITCODE -ne 0) {
    Dur "jev çalışmadı (çıkış kodu $LASTEXITCODE)."
}
Tamam 'Jev hazır'

Baslik 'Sonraki adımlar'
Bilgi '1. Giriş yapın (bir kez; Jev sizin yerinize giriş yapmaz):'
Bilgi '     codex login'
Bilgi '     claude auth login'
Bilgi "   codex ya da claude PATH'te değilse tam yolla çalıştırın; yolları 2. adımdaki komut gösterir (bkz. README)."
Bilgi "2. Ajanları ve bulunan CLI'ları görün:  $jev ajanlar"
Bilgi "   Girişleri gerçekten deneyin (az kota harcar):  $jev ajanlar --test"
Bilgi "3. Kota harcamayan kuru koşu:  $jev --kuru ""Yapılacaklar listesi CLI tasarla"""
Bilgi "4. İlk gerçek koşu:  $jev ""Python için sıcaklık ve uzunluk birim dönüştürücü CLI tasarla"""
Bilgi 'Ayrıntılar: README.md'
exit 0
