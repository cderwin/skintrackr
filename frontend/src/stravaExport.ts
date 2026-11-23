// The athlete's Strava data export: only its activity list (`activities.csv`) is uploaded, read here from the zip
// Strava emails them (gigabytes, mostly photos and videos, read by its directory without loading the rest) or
// taken as is if they chose the csv itself.

/** Where Strava athletes request their data export. */
export const STRAVA_EXPORT_URL = 'https://www.strava.com/athlete/delete_your_account'

const ACTIVITIES_CSV = 'activities.csv'

/** The `activities.csv` of a Strava export: the file itself, or read from the export's zip. */
export async function stravaActivitiesCsv(file: File): Promise<Blob> {
  if (file.name.toLowerCase().endsWith('.csv')) return file
  const { BlobReader, BlobWriter, ZipReader } = await import('@zip.js/zip.js')
  const reader = new ZipReader(new BlobReader(file), { useWebWorkers: false })
  try {
    const entries = await reader.getEntries().catch(() => {
      throw new Error("This isn't a zip file. Choose the zip of your Strava export, or its activities.csv.")
    })
    // at the zip's top level, or in a folder if it was unzipped and zipped again
    const entry = entries.find(
      (e) => !e.directory && (e.filename === ACTIVITIES_CSV || e.filename.endsWith(`/${ACTIVITIES_CSV}`)),
    )
    if (!entry || entry.directory) throw new Error(`This zip has no ${ACTIVITIES_CSV}: is it your Strava export?`)
    return await entry.getData(new BlobWriter('text/csv'))
  } finally {
    await reader.close()
  }
}
